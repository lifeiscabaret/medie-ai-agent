import os
import json
import base64
import time
import requests
from typing import TypedDict, Annotated, List, Union
from datetime import datetime

# 라이브러리 로드
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from azure.storage.blob import BlobServiceClient

from core.config import settings
from agent.prompts import SYSTEM_PROMPT

# 1. 에이전트 상태(State) 정의
class AgentState(TypedDict):
    """매디의 두뇌 역할을 하는 상태 저장소입니다."""
    user_id: str             # 사용자 고유 식별자 (예: "User_01")
    device_id: str           # NodeMCU 기기 ID (예: "NodeMCU_01")
    iot_status: dict         # 실시간 IoT 실시간 데이터 (기기에서 넘어온 생생한 정보), 구성: {"weight_change": 10.06, "timestamp": "2026-03-20 11:23:03", "rssi": -63, ...} 복약정보도 추가
    schedule: List[dict]     # 복약 스케줄 정보 (DB에서 읽어온 오늘의 목표), 구성: [{"pill_name": "비타민", "dosage_time": "13:00", "is_taken": False}, ...]
    next_step: str           # 에이전트 추론 및 제어 필드 (LLM이 결정하는 부분), 현재 에이전트가 판단한 다음 논리 단계 (예: "IDLE", "SEND_PUSH", "WAIT_FOR_CONFIRM", "RECORD_SUCCESS")
    action_required: str     # 앱 프론트엔드에게 보낼 구체적인 제어 신호 (예: "SHOW_CONFIRMATION_POPUP", "PLAY_DOG_BARK")
    response_text: str       # 대화 및 인터랙션 (사용자와의 소통 기록), 매디가 사용자에게 전송할 친절한 응답 텍스트
    messages: Annotated[List[str], "대화 로그"]    # LLM과의 전체 대화 이력 (LangGraph의 기억 저장소)
    user_confirmed: bool     # 복약 확정 상태 (Human-in-the-Loop 결과), 사용자가 앱에서 [예]를 눌렀는지 여부

# 2. 모델 설정 (조원의 Settings 활용으로 보안 강화)
llm = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    azure_deployment=settings.azure_openai_deployment_name,
    api_version=settings.azure_openai_api_version,
    api_key=settings.azure_openai_api_key,
    temperature=0.3 # 판단의 정확도를 위해 온도를 낮춤
)

# ---------------------------------------------------------
# 3. 노드 함수 정의 (빌드업 핵심)
# ---------------------------------------------------------
def monitor_iot_node(state: AgentState):
    """[Node 1] Azure Storage에서 최신 IoT 데이터를 가져와 디코딩"""
    print("\n[System] Azure IoT Storage 데이터 확인 중...")

    # 1. 연결 설정 (이미 설정된 환경변수나 문자열 사용), 여기서 조원의 settings를 최대한 활용하도록 재구성
    conn_str = "DefaultEndpointsProtocol=https;AccountName=mediehubstorage;AccountKey=fC/v/zFg9TRtClKXxHktHPxof2VFQKJikNmHIT4xL3GxirXB6y5dAIbvaveKs9ckqoB/LHw89IsZ+ASt7eLaCg==;EndpointSuffix=core.windows.net"
    container_name = "mediehubstoragecontainer"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service_client.get_container_client(container_name)

        # 2. 모든 파일 목록을 가져와서 가장 최근 파일(last_modified) 찾기
        blobs = list(container_client.list_blobs())
        if not blobs:
            print("(!) 저장된 데이터가 없습니다. 빈 상태로 다음 노드로 이동합니다.")
            return {**state, "iot_status": {}} # 데이터 없을 때 빈 딕셔너리 반환
        latest_blob = sorted(blobs, key=lambda x: x.last_modified, reverse=True)[0]

        # [추가] Azure 파일 시스템상의 마지막 수정 시간을 가져옴 (보험용)
        # UTC 시간을 한국 시간(KST)으로 변환하려면 +9시간 처리가 필요할 수 있으나, 일단 문자열화 합니다.
        file_time_str = latest_blob.last_modified.strftime('%Y-%m-%d %H:%M:%S')
        
        # 3. 데이터 다운로드 및 JSON 파싱
        blob_client = container_client.get_blob_client(latest_blob)
        # raw_content = json.loads(blob_client.download_blob().readall())
        content_str = blob_client.download_blob().readall().decode('utf-8')

        try:
            # 파일에 JSON이 여러 개 붙어 있을 경우, 첫 번째 것만 가져오기 위해 처리
            # (만약 모든 데이터를 보려면 split('}{') 등의 로직이 필요하지만, 
            #  최신 데이터 하나만 보는 게 목적이라면 아래 방식이 안전
            import json
            from json import JSONDecoder

            decoder = JSONDecoder()
            # 첫 번째 유효한 JSON 객체만 뽑아냅니다.
            raw_content, index = decoder.raw_decode(content_str)
            
        except json.JSONDecodeError:
            # 혹시 모르니 기존 방식도 백업으로 둡니다.
            raw_content = json.loads(content_str)
        
        # 4. 'Body' 필드의 Base64 문자열을 디코딩(해독)
        encoded_body = raw_content.get("Body", "")
        if encoded_body:
            decoded_bytes = base64.b64decode(encoded_body)
            decoded_json = json.loads(decoded_bytes) # 실제 아두이노 전송 데이터

            # [Time 로직 핵심]
            # 아두이노가 보낸 timestamp가 있으면 그것을 유지하고, 
            # 없거나 비어있으면 Azure 파일 시간을 주입합니다.
            if not decoded_json.get("timestamp"):
                decoded_json["timestamp"] = file_time_str
                print(f" -> [알림] 기기 내 시간 정보 없음. 파일 수정 시각으로 대체: {file_time_str}")
            else:
                print(f" -> [확인] 기기 전송 타임스탬프 사용: {decoded_json['timestamp']}")

            # 터미널에서 복약 상태 확인
            m = "O" if decoded_json.get("morning") else "X"
            l = "O" if decoded_json.get("lunch") else "X"
            e = "O" if decoded_json.get("evening") else "X"
            b = "O" if decoded_json.get("bedtime") else "X"
        
            print(f" -> [성공] 최신 감지된 무게 변화:: {decoded_json.get('weight_change')}g")
            print(f" -> [상태] 아침:{m} | 점심:{l} | 저녁:{e} | 취침:{b}")

            # 5. 상태(State) 업데이트 후 반환
            return {
                **state,
                "iot_status": decoded_json,
                "device_id": decoded_json.get("deviceId", "Unknown")
            }
        else:
            print("(!) 파일은 찾았으나 Body 데이터가 비어있습니다.")
            return state

    except Exception as err:
        # 연결 문자열이 틀렸거나 네트워크 문제일 경우 실행됨
        print(f"(!) 데이터 로드 중 오류 발생: {err}")
        # 실패하더라도 프로그램이 멈추지 않게 기존 상태(state)를 그대로 넘겨줍니다.
        return state

def analyze_schedule_node(state: AgentState):
    """[Node 2] 복약 스케줄 대조"""
    print("[System Log] 오늘의 복약 일정 확인 중...")
    # 향후 DB 연동을 위해 구조만 유지
    return {**state, "schedule": [{"pill_name": "비타민", "time": "13:00", "is_taken": False}]}

def maddy_reasoning_node(state: AgentState):
    """[Node 3] 매디의 최종 추론 (조원의 프롬프트 활용)"""
    print("[System Log] 매디가 생각 중...")

    # 프롬프트에 형식을 강하게 지시
    format_instruction = "\n\nJSON 형식으로만 대답해: {'reply': '...', 'command': '...', 'target': '...'}"
    
    # 조원이 작성한 SYSTEM_PROMPT를 사용하여 일관성 유지
    messages = [
        SystemMessage(content=SYSTEM_PROMPT + format_instruction),
        HumanMessage(content=f"현재 모드: {state.get('next_step')}\n데이터: {state['iot_status']}\n대화기록: {state['messages']}")
    ]

    response = llm.invoke(messages)
    content = response.content.strip()

    # 가끔 AI가 ```json ... ``` 이렇게 마크다운을 섞어줄 때를 대비한 안전장치
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()

    try:
        # 조원이 의도한 JSON 형식으로 파싱 시도
        ai_res = json.loads(response.content)
        print(f" -> [매디 응답]: {ai_res.get('reply')}")
        return {
            **state,
            "response_text": ai_res.get("reply", "멍! 무슨 일인가멍?"),
            "action_required": ai_res.get("command", "NONE"),
            "next_step": ai_res.get("target", "IDLE")
        }
    except:
        # JSON 파싱 실패하더라도 최소한의 JSON 구조는 유지해서 반환
        print(" -> [주의] JSON 파싱 실패!")
        return {**state, "response_text": response.content, "next_step": "IDLE"}

# ---------------------------------------------------------
# 4. 그래프 구성 및 컴파일
# ---------------------------------------------------------
workflow = StateGraph(AgentState)

workflow.add_node("monitor_iot", monitor_iot_node)
workflow.add_node("analyze_schedule", analyze_schedule_node)
workflow.add_node("maddy_reasoning", maddy_reasoning_node)

workflow.set_entry_point("monitor_iot")
workflow.add_edge("monitor_iot", "analyze_schedule")
workflow.add_edge("analyze_schedule", "maddy_reasoning")
workflow.add_edge("maddy_reasoning", END)

# 최종 앱 객체
app = workflow.compile()
print("Maddy Agent 빌드 완료!")

# ---------------------------------------------------------
# 6. 외부 전송 함수 (DB 연동용)
# ---------------------------------------------------------
def send_to_joone_fastapi(state: AgentState):
    """
    분석된 결과를 DB FastAPI 서버 주소로 POST 전송합니다.
    """
    # 조원이 알려줄 실제 서버 주소로 수정 필요 (예: http://1.2.3.4:8000/api/pill-check)
    JOONE_API_URL = "https://medichubs-backend.azurewebsites.net/Arduino" 

    # 조원 서버가 받기 편한 형태로 데이터 가공
    payload = {
        "user_id": state["user_id"],
        "device_id": state["device_id"],
        "morning": state["iot_status"].get("morning"),  # 아두이노에서 온 데이터
        "lunch": state["iot_status"].get("lunch"),
        "evening": state["iot_status"].get("evening"),
        "bedtime": state["iot_status"].get("bedtime"),
        "maddy_message": state["response_text"],
        "action_required": state["action_required"],
        "weight_change": state["iot_status"].get("weight_change", 0),
        "is_taken": state["user_confirmed"]
    }

    try:
        print(f"[Maddy] 조원 서버로 데이터 전송 중... ({JOONE_API_URL})")
        response = requests.post(JOONE_API_URL, json=payload, timeout=5)
        
        if response.status_code == 200:
            print(f"✅ [전송 성공] 조원 서버 응답: {response.json()}")
        else:
            print(f"❌ [전송 실패] 상태 코드: {response.status_code}")
            
    except Exception as err:
        print(f"(!) 조원 서버 연결 오류: {err}")

# ---------------------------------------------------------
# 6. 조원의 main.py가 호출할 인터페이스 함수
# ---------------------------------------------------------
def get_medie_response(user_message: str, current_mode: str):
    """조원의 main.py가 호출하는 입구"""
    # 1. 초기 상태 설정
    initial_state = {
        "user_id": "User_01",
        "device_id": "Unknown",
        "iot_status": {}, # Node 1 함수가 채워줌
        "schedule": [],   # Node 2 함수가 채워줌
        "next_step": current_mode,
        "action_required": "NONE",
        "response_text": "",
        "messages": [user_message],
        "user_confirmed": False
    }

    # 여기서 monitor_iot -> analyze_schedule -> reasoning이 순서대로 돕니다.
    final_result = app.invoke(initial_state)

    # 앱에 응답을 주기 전에 서버에도 이 내용을 알려줘야 DB에 기록됩니다!
    try:
        send_to_joone_fastapi(final_result)
        print("✅ [Sync] 조원 서버에 분석 데이터 전송 완료")
    except Exception as err:
        print(f"⚠️ [Sync] 조원 서버 전송 실패 (무시하고 진행): {err}")
    
    # 4. 조원이 요구한 형태(reply, command, target)로 최종 반환
    # 이 값은 main.py의 FastAPI를 통해 앱 화면에 바로 뜹니다.
    return {
        "reply": final_result["response_text"],
        "command": final_result["action_required"],
        "target": final_result["next_step"]
    }