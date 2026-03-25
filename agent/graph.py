import os
import json
import base64
import time
import requests
from typing import TypedDict, Annotated, List, Literal
from datetime import datetime
from json import JSONDecoder

from pydantic import BaseModel, Field, ValidationError
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from azure.storage.blob import BlobServiceClient

from core.config import settings
from agent.prompts import SYSTEM_PROMPT


# 1. Pydantic 스키마 정의
class MedicationData(BaseModel):
    """IoT 기기에서 들어오는 입력 데이터 규격"""
    device_id: str = Field(default="Unknown", alias="deviceId")
    timestamp: str = Field(default="")
    morning: bool = Field(default=False)
    lunch: bool = Field(default=False)
    evening: bool = Field(default=False)
    bedtime: bool = Field(default=False)
    action: str = Field(default="NONE")
    weight_change: float = Field(default=0.0)
    rssi: int = Field(default=0)
    pill_status: str = Field(default="UNKNOWN")
    epoch: int = Field(default=0)
    zone: int = Field(default=1)
    free_heap: int = Field(default=0)

    model_config = {
        "populate_by_name": True,
        "extra": "ignore"
    }


class IntentClassification(BaseModel):
    """1차 의도 분류"""
    intent: Literal["NAVIGATE", "COMPLETE_DOSE", "SET_ALARM", "IOT_EVENT", "CHAT"] = Field(
        description="사용자 의도 분류"
    )
    reason: str = Field(description="분류 이유")


class AgentResponse(BaseModel):
    """LLM 출력 규격"""
    reply: str = Field(description="매디 응답 텍스트")
    command: str = Field(description="앱 제어 신호")
    target: str = Field(description="이동할 화면")
    show_confirmation: bool = Field(default=False)
    params: dict = Field(default={})


class BackendPayload(BaseModel):
    """DB 서버로 전송할 데이터 규격"""
    user_id: str
    device_id: str
    morning: bool
    lunch: bool
    evening: bool
    bedtime: bool
    weight_change: float
    is_taken: bool
    maddy_message: str
    action_required: str = Field(default="NONE")

    model_config = {"populate_by_name": True}


# 2. 에이전트 상태 정의
class AgentState(TypedDict):
    user_id: str
    device_id: str
    iot_status: dict
    schedule: List[dict]
    intent: str
    next_step: str
    action_required: str
    response_text: str
    messages: Annotated[List[str], "대화 로그"]
    user_confirmed: bool
    show_confirmation: bool
    params: dict


# 3. 모델 설정
llm = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    azure_deployment=settings.azure_openai_deployment_name,
    api_version=settings.azure_openai_api_version,
    api_key=settings.azure_openai_api_key,
    temperature=0.3
)

structured_llm = llm.with_structured_output(AgentResponse)
intent_llm = llm.with_structured_output(IntentClassification)


# =========================================================
# 4. DB 연결 함수 (조원 API 나오면 여기만 채우면 됨)
# =========================================================
async def get_user_profile(user_id: str):
    """TODO: 조원 API 연결"""
    return {
        "habit_strength": "medium",
        "miss_risk_score": 0.5,
        "preferred_time_windows": {
            "morning": "08:00-09:00",
            "lunch": "12:00-13:00"
        },
        "pattern_change_count": 0,
        "notes": ""
    }

async def save_medication_log(user_id: str, taken_at: str, pill_name: str, source: str = "MANUAL"):
    """TODO: 조원 API 연결 - POST /medication-logs"""
    print(f"[DB] 복용 기록 저장: {user_id} / {pill_name} / {taken_at}")
    pass

async def update_user_profile(user_id: str, data: dict):
    """TODO: 조원 API 연결 - PATCH /users/{user_id}/profile"""
    print(f"[DB] 프로필 업데이트: {user_id} / {data}")
    pass


# 5. 노드 함수 정의
def monitor_iot_node(state: AgentState):
    """[Node 1] Azure Storage에서 최신 IoT 데이터 로드"""
    print("\n[System] Azure IoT Storage 데이터 확인 중...")

    conn_str = settings.azure_storage_connection_string
    container_name = "mediehubstoragecontainer"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service_client.get_container_client(container_name)

        blobs = list(container_client.list_blobs())
        if not blobs:
            print("(!) 저장된 데이터 없음")
            return {**state, "iot_status": {}}

        latest_blob = sorted(blobs, key=lambda x: x.last_modified, reverse=True)[0]
        file_time_str = latest_blob.last_modified.strftime('%Y-%m-%d %H:%M:%S')

        blob_client = container_client.get_blob_client(latest_blob)
        content_str = blob_client.download_blob().readall().decode('utf-8')

        try:
            decoder = JSONDecoder()
            raw_content, _ = decoder.raw_decode(content_str)
        except json.JSONDecodeError:
            raw_content = json.loads(content_str)

        encoded_body = raw_content.get("Body", "")
        if encoded_body:
            decoded_bytes = base64.b64decode(encoded_body)
            decoded_json = json.loads(decoded_bytes)

            if not decoded_json.get("timestamp"):
                decoded_json["timestamp"] = file_time_str

            try:
                validated_data = MedicationData(**decoded_json)
                clean_dict = validated_data.model_dump()
                print(f" -> [검증 성공] 무게변화: {clean_dict['weight_change']}g")
                return {**state, "iot_status": clean_dict, "device_id": clean_dict['device_id']}
            except ValidationError as e:
                print(f"(!) Pydantic 검증 실패: {e}")
                return state
        else:
            return state

    except Exception as err:
        print(f"(!) IoT 데이터 로드 실패: {err}")
        return state


def analyze_schedule_node(state: AgentState):
    """[Node 2] 복약 스케줄 대조"""
    print("[System] 복약 스케줄 확인 중...")
    return {**state, "schedule": [{"pill_name": "비타민", "time": "13:00", "is_taken": False}]}


def classify_intent_node(state: AgentState):
    """[Node 3] 사용자 의도 분류 - 핵심 분기점!"""
    print("[System] 사용자 의도 분류 중...")

    user_message = state["messages"][-1] if state["messages"] else ""
    iot_data = state.get("iot_status", {})
    weight_change = iot_data.get("weight_change", 0)

    # IoT 이벤트 우선 체크
    if abs(weight_change) > 1.0:
        print(f" -> [IoT 감지] 무게 변화: {weight_change}g → IOT_EVENT")
        return {**state, "intent": "IOT_EVENT"}

    classify_messages = [
        SystemMessage(content="""
사용자 메시지를 보고 의도를 분류하세요.
- NAVIGATE: 화면 이동 요청 (약국 찾아줘, 스캔해줘, 내 약 보여줘 등)
- COMPLETE_DOSE: 복약 완료 (약 먹었어, 복용했어, 먹었다 등)
- SET_ALARM: 알람 시간 변경 (8시로 바꿔줘, 알람 설정해줘 등)
- IOT_EVENT: IoT 기기 관련
- CHAT: 그 외 일반 대화, 약 정보 질문 등
"""),
        HumanMessage(content=f"사용자 메시지: {user_message}")
    ]

    try:
        result = intent_llm.invoke(classify_messages)
        print(f" -> [의도 분류] {result.intent} / 이유: {result.reason}")
        return {**state, "intent": result.intent}
    except Exception as e:
        print(f"(!) 의도 분류 실패: {e}")
        return {**state, "intent": "CHAT"}


def navigate_node(state: AgentState):
    """[Node 4] 화면 이동 처리"""
    print("[System] 화면 이동 처리 중...")

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"사용자가 화면 이동을 원합니다. 메시지: {state['messages'][-1]}\n어떤 화면으로 이동할지 결정하고 친절하게 안내해주세요.")
    ]

    try:
        ai_res = structured_llm.invoke(messages)
        return {
            **state,
            "response_text": ai_res.reply,
            "action_required": "NAVIGATE",
            "next_step": ai_res.target,
            "show_confirmation": False,
            "params": {}
        }
    except Exception as e:
        print(f"(!) navigate_node 실패: {e}")
        return {
            **state,
            "response_text": "화면 이동할게요! 멍!",
            "action_required": "NAVIGATE",
            "next_step": "HOME",
            "show_confirmation": False,
            "params": {}
        }


def complete_dose_node(state: AgentState):
    """[Node 5] 복약 완료 처리"""
    print("[System] 복약 완료 처리 중...")

    taken_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # TODO: await save_medication_log(state["user_id"], taken_at, "복용약")

    return {
        **state,
        "response_text": "복용 완료로 기록했어요! 건강을 위해 잘 챙겨드시는 주인님 최고예요! 멍멍!",
        "action_required": "COMPLETE_DOSE",
        "next_step": "NONE",
        "show_confirmation": False,
        "params": {"taken_at": taken_at}
    }


def set_alarm_node(state: AgentState):
    """[Node 6] 알람 시간 변경 처리"""
    print("[System] 알람 시간 변경 처리 중...")

    messages = [
        SystemMessage(content="""
사용자가 알람 시간을 변경하고 싶어합니다.
메시지에서 시간을 추출해서 HH:MM 형식으로 변환하세요.
예: "8시" → "08:00", "오후 3시 반" → "15:30"
반드시 params에 time과 pillId를 포함하세요. pillId는 "all"로 설정하세요.
"""),
        HumanMessage(content=f"사용자 메시지: {state['messages'][-1]}")
    ]

    try:
        ai_res = structured_llm.invoke(messages)
        return {
            **state,
            "response_text": ai_res.reply,
            "action_required": "SET_ALARM",
            "next_step": "ALARM",
            "show_confirmation": False,
            "params": ai_res.params
        }
    except Exception as e:
        print(f"(!) set_alarm_node 실패: {e}")
        return {
            **state,
            "response_text": "알람 시간을 말씀해주세요! 멍!",
            "action_required": "NONE",
            "next_step": "NONE",
            "show_confirmation": False,
            "params": {}
        }


def iot_action_node(state: AgentState):
    """[Node 7] IoT 이벤트 처리 - 자동 복약 감지"""
    print("[System] IoT 이벤트 처리 중...")

    iot_data = state.get("iot_status", {})
    weight_change = iot_data.get("weight_change", 0)
    print(f" -> 무게 변화 감지: {weight_change}g → 복약 확인 팝업 표시")

    return {
        **state,
        "response_text": "약통에서 움직임이 감지됐어요! 방금 약 드셨나요? 멍멍!",
        "action_required": "SHOW_CONFIRMATION",
        "next_step": "NONE",
        "show_confirmation": True,
        "params": {
            "weight_change": weight_change,
            "detected_at": iot_data.get("timestamp", "")
        }
    }


def chat_node(state: AgentState):
    """[Node 8] 일반 대화 처리"""
    print("[System] 일반 대화 처리 중...")

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"현재 모드: {state.get('next_step')}\n데이터: {state['iot_status']}\n대화기록: {state['messages']}")
    ]

    try:
        ai_res = structured_llm.invoke(messages)
        print(f" -> [매디 응답]: {ai_res.reply}")
        return {
            **state,
            "response_text": ai_res.reply,
            "action_required": ai_res.command,
            "next_step": ai_res.target,
            "show_confirmation": ai_res.show_confirmation,
            "params": ai_res.params
        }
    except Exception as e:
        print(f"(!) chat_node 실패: {e}")
        return {
            **state,
            "response_text": "멍! 지금은 조금 헷갈린다멍. 잠시 후에 다시 말해달라멍!",
            "action_required": "NONE",
            "next_step": "IDLE",
            "show_confirmation": False,
            "params": {}
        }



# 6. 라우팅 함수
def route_by_intent(state: AgentState) -> str:
    intent = state.get("intent", "CHAT")
    print(f"[Router] 의도: {intent} → 해당 노드로 이동")

    routes = {
        "NAVIGATE": "navigate",
        "COMPLETE_DOSE": "complete_dose",
        "SET_ALARM": "set_alarm",
        "IOT_EVENT": "iot_action",
        "CHAT": "chat"
    }
    return routes.get(intent, "chat")


# 7. 그래프 구성 및 컴파일
workflow = StateGraph(AgentState)

workflow.add_node("monitor_iot", monitor_iot_node)
workflow.add_node("analyze_schedule", analyze_schedule_node)
workflow.add_node("classify_intent", classify_intent_node)
workflow.add_node("navigate", navigate_node)
workflow.add_node("complete_dose", complete_dose_node)
workflow.add_node("set_alarm", set_alarm_node)
workflow.add_node("iot_action", iot_action_node)
workflow.add_node("chat", chat_node)

workflow.set_entry_point("monitor_iot")
workflow.add_edge("monitor_iot", "analyze_schedule")
workflow.add_edge("analyze_schedule", "classify_intent")

workflow.add_conditional_edges(
    "classify_intent",
    route_by_intent,
    {
        "navigate": "navigate",
        "complete_dose": "complete_dose",
        "set_alarm": "set_alarm",
        "iot_action": "iot_action",
        "chat": "chat"
    }
)

workflow.add_edge("navigate", END)
workflow.add_edge("complete_dose", END)
workflow.add_edge("set_alarm", END)
workflow.add_edge("iot_action", END)
workflow.add_edge("chat", END)

app = workflow.compile()
print("✅ Maddy Agent 빌드 완료! (LangGraph 분기 탑재)")


# 8. 외부 전송 함수
def send_to_joone_fastapi(state: AgentState):
    """DB FastAPI 서버로 POST 전송 - Pydantic 검증 포함"""
    JOONE_API_URL = "http://20.106.40.121/arduino"
    iot = state.get("iot_status", {})

    try:
        payload_data = BackendPayload(
            user_id=state.get("user_id", "Unknown"),
            device_id=state.get("device_id", "Unknown"),
            morning=iot.get("morning", False),
            lunch=iot.get("lunch", False),
            evening=iot.get("evening", False),
            bedtime=iot.get("bedtime", False),
            weight_change=float(iot.get("weight_change", 0.0)),
            is_taken=state.get("user_confirmed", False),
            maddy_message=state.get("response_text", ""),
            action_required=state.get("action_required", "NONE")
        )

        print(f"[Maddy] DB 서버로 전송 중... ({JOONE_API_URL})")
        response = requests.post(JOONE_API_URL, json=payload_data.model_dump(), timeout=5)

        if response.status_code == 200:
            print(f"✅ 전송 성공: {response.json()}")
        else:
            print(f"❌ 전송 실패: {response.status_code} | {response.text}")

    except ValidationError as val_err:
        print(f"⚠️ [데이터 규격 위반]: {val_err}")
    except requests.exceptions.RequestException as req_err:
        print(f"(!) [네트워크 오류]: {req_err}")
    except Exception as err:
        print(f"(!) [시스템 오류]: {err}")


def send_push_notification(user_id: str, title: str, body: str):
    """앱 푸시 알림 전송 - TODO: 조원 API 연결 후 활성화"""
    PUSH_API_URL = "http://20.106.40.121/push/send"

    push_payload = {
        "user_id": user_id,
        "title": title,
        "body": body,
        "priority": "high"
    }

    try:
        print(f"📢 [Push] 앱 푸시 발송 요청 중...")
        response = requests.post(PUSH_API_URL, json=push_payload, timeout=3)
        if response.status_code == 200:
            print("✅ [Push 성공]")
        else:
            print(f"⚠️ [Push 실패] 상태 코드: {response.status_code}")
    except Exception as err:
        print(f"(!) [Push 오류]: {err}")


# 9. 메인 인터페이스 함수
def get_medie_response(user_message: str, current_mode: str):
    """main.py가 호출하는 입구"""
    initial_state = {
        "user_id": "User_01",
        "device_id": "Unknown",
        "iot_status": {},
        "schedule": [],
        "intent": "CHAT",
        "next_step": current_mode,
        "action_required": "NONE",
        "response_text": "",
        "messages": [user_message],
        "user_confirmed": False,
        "show_confirmation": False,
        "params": {}
    }

    final_result = app.invoke(initial_state)

    try:
        send_to_joone_fastapi(final_result)
    except Exception as err:
        print(f"⚠️ DB 서버 전송 실패: {err}")

    return {
        "reply": final_result["response_text"],
        "command": final_result["action_required"],
        "target": final_result["next_step"],
        "show_confirmation": final_result.get("show_confirmation", False),
        "params": final_result.get("params", {})
    }