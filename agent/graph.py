import os
import json
import base64
import time
import urllib.parse
import requests
from typing import TypedDict, Annotated, List, Literal
from json import JSONDecoder


from pydantic import BaseModel, Field, ValidationError
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from azure.storage.blob import BlobServiceClient
from datetime import datetime, timedelta, timezone

from core.config import settings
from agent.prompts import SYSTEM_PROMPT


# 1. Pydantic 스키마 정의
class MedicationData(BaseModel):
    device_id: str = Field(default="Unknown", alias="deviceId")
    user_id: str = Field(default="Unknown", alias="userId")  # user_id 추가
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
    intent: Literal[
        "NAVIGATE", "COMPLETE_DOSE", "SET_ALARM",
        "TOGGLE_ALL_ALARMS", "DELETE_ALL_ALARMS",
        "IOT_EVENT", "SEARCH_DRUG", "WRITE_POST", "POST_SUBMIT",
        "CHECK_HISTORY", "DRUG_INFO", "CHAT"
    ] = Field(description="사용자 의도 분류")
    reason: str = Field(description="분류 이유")


class AlarmParams(BaseModel):
    time: str = Field(default="")
    pillId: str = Field(default="all")
    taken_at: str = Field(default="")
    weight_change: float = Field(default=0.0)
    detected_at: str = Field(default="")
    keyword: str = Field(default="")
    title: str = Field(default="")
    content: str = Field(default="")
    board_type: str = Field(default="free")
    author: str = Field(default="")
    enabled: bool = Field(default=True)

    model_config = {"extra": "forbid"}


class AgentResponse(BaseModel):
    reply: str = Field(description="매디 응답 텍스트")
    command: str = Field(description="앱 제어 신호")
    target: str = Field(description="이동할 화면")
    show_confirmation: bool = Field(default=False)
    params: AlarmParams = Field(default_factory=AlarmParams)


class BackendPayload(BaseModel):
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
    pill_history: List[dict]
    chat_history: List[dict]
    last_confirmed_timestamp: str  # 중복 팝업 방지
    push_token: str  # ← 추가

# 3. 모델 설정
fast_llm = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    azure_deployment=settings.azure_openai_deployment_name,
    api_version=settings.azure_openai_api_version,
    api_key=settings.azure_openai_api_key,
    temperature=0.3,
    max_tokens=150,
)
fast_structured = fast_llm.with_structured_output(AgentResponse)

# 긴 응답용 (drug_info, check_history)
rich_llm = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    azure_deployment=settings.azure_openai_deployment_name,
    api_version=settings.azure_openai_api_version,
    api_key=settings.azure_openai_api_key,
    temperature=0.3,
    max_tokens=400,
)
rich_structured = rich_llm.with_structured_output(AgentResponse)

# 기존 llm (drug_info 약 이름 추출용)
llm = fast_llm

# 의도 분류용 (기존 유지)
intent_llm = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    azure_deployment=settings.azure_openai_deployment_name,
    api_version=settings.azure_openai_api_version,
    api_key=settings.azure_openai_api_key,
    temperature=0.1,
    max_tokens=80,
).with_structured_output(IntentClassification)


# 4. DB 연결 함수
async def get_user_profile(user_id: str):
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
    print(f"[DB] 복용 기록 저장: {user_id} / {pill_name} / {taken_at}")

async def update_user_profile(user_id: str, data: dict):
    print(f"[DB] 프로필 업데이트: {user_id} / {data}")


# =========================================================
# 5. 식약처 API
# =========================================================
def fetch_drug_info(drug_name: str) -> dict:
    try:
        api_key = settings.drug_api_key
        endpoint = settings.drug_api_endpoint

        params = {
            "serviceKey": api_key,
            "itemName": drug_name,
            "pageNo": "1",
            "numOfRows": "3",
            "type": "json"
        }

        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        response = requests.get(url, timeout=5)
        data = response.json()

        items = data.get("body", {}).get("items", [])
        if not items:
            return {}

        item = items[0]
        return {
            "name": item.get("itemName", ""),
            "effect": item.get("efcyQesitm", ""),
            "usage": item.get("useMethodQesitm", ""),
            "warning": item.get("atpnWarnQesitm", ""),
            "interaction": item.get("intrcQesitm", ""),
            "side_effect": item.get("seQesitm", ""),
        }
    except Exception as e:
        print(f"(!) 식약처 API 오류: {e}")
        return {}


# =========================================================
# 6. 노드 함수 정의
# =========================================================
def monitor_iot_node(state: AgentState):
    user_message = state["messages"][0] if state["messages"] else ""
    if user_message and user_message.strip():
        print("[System] 사용자 메시지 감지 → IoT 로드 스킵")
        return state

    print("\n[System] Azure IoT Storage 데이터 확인 중...")
    conn_str = settings.azure_storage_connection_string
    container_name = "mediehubstoragecontainer"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service_client.get_container_client(container_name)

        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        if now.hour < 4:
            start_time = now.replace(hour=4, minute=0, second=0, microsecond=0) - timedelta(days=1)
        else:
            start_time = now.replace(hour=4, minute=0, second=0, microsecond=0)

        blobs = list(container_client.list_blobs())
        relevant_blobs = [
            b for b in blobs
            if b.last_modified.astimezone(kst) >= start_time
        ]

        if not relevant_blobs:
            return {**state, "iot_status": {}}

        aggregated_status = {
            "morning": False, "lunch": False,
            "evening": False, "bedtime": False,
            "weight_change": 0.0, "deviceId": "Unknown", "timestamp": ""
        }

        for blob_info in sorted(relevant_blobs, key=lambda x: x.last_modified):
            try:
                blob_client = container_client.get_blob_client(blob_info)
                content_str = blob_client.download_blob().readall().decode('utf-8')

                try:
                    decoder = JSONDecoder()
                    raw_content, _ = decoder.raw_decode(content_str)
                except json.JSONDecodeError:
                    raw_content = json.loads(content_str)

                file_time = blob_info.last_modified.astimezone(kst).strftime('%Y-%m-%d %H:%M:%S')
                encoded_body = raw_content.get("Body", "")
                if not encoded_body:
                    continue

                if isinstance(encoded_body, dict):
                    decoded_json = encoded_body
                elif isinstance(encoded_body, str):
                    decoded_bytes = base64.b64decode(encoded_body)
                    decoded_json = json.loads(decoded_bytes)
                else:
                    continue

                action_str = str(decoded_json.get("action", "")).upper()

                if decoded_json.get("morning") is True or "MORNING" in action_str:
                    aggregated_status["morning"] = True
                if decoded_json.get("lunch") is True or "LUNCH" in action_str:
                    aggregated_status["lunch"] = True
                if decoded_json.get("evening") is True or "EVENING" in action_str:
                    aggregated_status["evening"] = True
                if decoded_json.get("bedtime") is True or "BEDTIME" in action_str:
                    aggregated_status["bedtime"] = True

                aggregated_status["weight_change"] = decoded_json.get("weight_change", 0.0)
                aggregated_status["deviceId"] = decoded_json.get("deviceId", "Unknown")
                aggregated_status["timestamp"] = decoded_json.get("timestamp") or file_time

            except Exception as err:
                print(f" -> [경고] 파일 해석 중 오류(무시): {err}")
                continue

        try:
            validated_data = MedicationData(**aggregated_status)
            clean_dict = validated_data.model_dump()

            m = "O" if clean_dict['morning'] else "X"
            l = "O" if clean_dict['lunch'] else "X"
            e = "O" if clean_dict['evening'] else "X"
            b = "O" if clean_dict['bedtime'] else "X"

            print(f"\n[Maddy Summary] 아침({m}) 점심({l}) 저녁({e}) 취침전({b})")

            return {
                **state,
                "iot_status": clean_dict,
                "device_id": clean_dict['device_id']
            }
        except ValidationError as val_err:
            print(f"(!) Pydantic 검증 오류: {val_err}")
            return state

    except Exception as err:
        print(f"(!) 데이터 로드 중 오류 발생: {err}")
        return state


def analyze_schedule_node(state: AgentState):
    user_message = state["messages"][0] if state["messages"] else ""
    if user_message and user_message.strip():
        return state  # 사용자 메시지 있으면 즉시 스킵
    print("[System] 복약 스케줄 확인 중...")
    return {**state, "schedule": [{"pill_name": "비타민", "time": "13:00", "is_taken": False}]}


def classify_intent_node(state: AgentState):
    print("[System] 사용자 의도 분류 중...")

    user_message = state["messages"][-1] if state["messages"] else ""

    quick_rules = [
        (["먹었어", "먹었다", "복용했어", "응 먹었"], "COMPLETE_DOSE"),
        (["알람 다 켜", "알람 켜줘", "모든 알람 켜"], "TOGGLE_ALL_ALARMS"),
        (["알람 다 꺼", "알람 꺼줘", "모든 알람 꺼"], "TOGGLE_ALL_ALARMS"),
        (["알람 다 지워", "알람 삭제", "모든 알람 삭제"], "DELETE_ALL_ALARMS"),
        (["약 먹었", "방금 먹었"], "COMPLETE_DOSE"),
        (["약 검색", "약 찾아줘", "검색해줘"], "SEARCH_DRUG"),
        (["게시글 써줘", "후기 써줘", "글 써줘", "작성해줘"], "WRITE_POST"),
        (["올려줘", "등록해줘", "게시판에 올려", "업로드해줘"], "POST_SUBMIT"),
    ]


    for keywords, intent in quick_rules:
        if any(k in user_message for k in keywords):
            print(f" -> [빠른 분류] {intent}")
            return {**state, "intent": intent}

    # 기존 코드 (IoT 체크 + LLM 분류)
    iot_data = state.get("iot_status", {})
    weight_change = iot_data.get("weight_change", 0)

    # IoT 이벤트 - 이미 확인한 타임스탬프는 무시
    if abs(weight_change) > 1.0:
        timestamp_str = iot_data.get("timestamp", "")
        last_confirmed = state.get("last_confirmed_timestamp", "")
        is_recent = False
        try:
            iot_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
            diff = (datetime.now() - iot_time).total_seconds()
            is_recent = diff < 300
        except:
            is_recent = False

        if is_recent and timestamp_str != last_confirmed:
            print(f" -> [IoT 감지] 무게 변화: {weight_change}g → IOT_EVENT")
            return {**state, "intent": "IOT_EVENT"}
        else:
            print(f" -> [IoT 무시] 오래된 데이터이거나 이미 확인한 데이터")

    classify_messages = [
        SystemMessage(content="""사용자 메시지를 보고 의도를 분류하세요.
- NAVIGATE: 화면 이동 요청 (약국, 스캔, 내 약, 알람, 마이페이지, 커뮤니티, 히스토리 등)
- COMPLETE_DOSE: 복약 완료 (약 먹었어, 먹었다, 응 먹었어 등)
- SET_ALARM: 특정 알람 시간 변경 (8시로 바꿔줘 등)
- TOGGLE_ALL_ALARMS: 모든 알람 켜기/끄기 (알람 다 켜줘, 알람 다 꺼줘)
- DELETE_ALL_ALARMS: 모든 알람 삭제 (알람 다 지워줘)
- IOT_EVENT: IoT 기기 관련
- SEARCH_DRUG: 약 검색 (타이레놀 찾아줘, OO약 검색해줘)
- WRITE_POST: 게시글 작성 (후기 써줘, 게시판에 올려줘)
- POST_SUBMIT: 게시글 바로 등록 (올려줘, 등록해줘, 업로드해줘)  ← 이거 추가
- CHECK_HISTORY: 복약 내역 확인 (오늘 약 먹었어?, 이번주 언제 안먹었어?, 4월 복용내역)
- DRUG_INFO: 약 부작용/효능/주의사항 질문 (타이레놀 부작용 뭐야?)
- CHAT: 그 외 일반 대화"""),
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
    chat_history = state.get("chat_history", [])
    last_msg = chat_history[-1]["content"] if chat_history else ""

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"이전 대화: {last_msg}\n사용자 요청: {state['messages'][-1]}\n화면 이동 결정 후 친절하게 안내해주세요.")
    ]
    try:
        ai_res = fast_structured.invoke(messages)
        return {**state, "response_text": ai_res.reply, "action_required": "NAVIGATE",
                "next_step": ai_res.target, "show_confirmation": False, "params": {}}
    except Exception as e:
        print(f"(!) navigate_node 실패: {e}")
        return {**state, "response_text": "죄송해요, 다시 말씀해주세요!", "action_required": "NONE",
                "next_step": "NONE", "show_confirmation": False, "params": {}}


def complete_dose_node(state: AgentState):
    print("[System] 복약 완료 처리 중...")
    taken_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    iot_data = state.get("iot_status", {})
    iot_timestamp = iot_data.get("timestamp", taken_at)

    # ✅ 조원 DB에 복약 기록 저장
    try:
        requests.post("http://20.106.40.121/api/history", json={
            "user_id": state.get("user_id", "User_01"),
            "pill_name": "복약",
            "scheduled_time": taken_at[11:16],
            "taken_at": taken_at
        }, timeout=3)
        print("✅ 복약 이력 DB 저장 완료")
    except Exception as e:
        print(f"⚠️ 복약 이력 저장 실패 (무시): {e}")

    new_record = {
        "date": taken_at[:10],
        "time": taken_at[11:16],
        "taken": True,
        "source": "confirmed"
    }
    current_history = state.get("pill_history", [])
    updated_history = current_history + [new_record]

    # ✅ 패턴 분석
    pattern = analyze_pill_pattern(updated_history)
    
    if pattern and pattern.get("suggest"):
        avg_time = pattern["avg_time"]
        count = pattern["sample_count"]
        reply = (
            f"복용 완료로 기록했어요! 😊 "
            f"최근 {count}회 복약 기록을 보니 "
            f"평균 {avg_time}에 드시네요. "
            f"알람을 {avg_time}으로 변경해드릴까요?"
        )
        # 알람 변경 제안
        action = "SET_ALARM"
        params = {"time": avg_time, "pillId": "all"}
        print(f"[패턴 감지] 평균 복약 시간 {avg_time} → 알람 변경 제안")
    else:
        reply = "복용 완료로 기록했어요! 😊"
        action = "COMPLETE_DOSE"
        params = {"taken_at": taken_at}

    return {
        **state,
        "response_text": reply,
        "action_required": action,
        "next_step": "NONE",
        "show_confirmation": False,
        "params": params,
        "pill_history": updated_history,
        "last_confirmed_timestamp": iot_timestamp,
        "user_confirmed": True,
    }


def set_alarm_node(state: AgentState):
    print("[System] 알람 시간 변경 처리 중...")
    messages = [
        SystemMessage(content="""사용자가 알람 시간을 변경하고 싶어합니다.
메시지에서 시간을 추출해서 HH:MM 형식으로 변환하세요.
예: "8시" → "08:00", "오후 3시 반" → "15:30"
반드시 params에 time과 pillId를 포함하세요. pillId는 "all"로 설정하세요."""),
        HumanMessage(content=f"사용자 메시지: {state['messages'][-1]}")
    ]
    try:
        ai_res = fast_structured.invoke(messages)
        return {**state, "response_text": ai_res.reply, "action_required": "SET_ALARM",
                "next_step": "ALARM", "show_confirmation": False, "params": ai_res.params.model_dump()}
    except Exception as e:
        return {**state, "response_text": "알람 시간을 말씀해주세요!", "action_required": "NONE",
                "next_step": "NONE", "show_confirmation": False, "params": {}}


def toggle_all_alarms_node(state: AgentState):
    print("[System] 모든 알람 켜기/끄기 처리 중...")
    user_message = state["messages"][-1]
    enabled = "켜" in user_message or "on" in user_message.lower()
    status = "켰어요" if enabled else "껐어요"
    return {
        **state,
        "response_text": f"모든 알람을 {status}! 💊",
        "action_required": "TOGGLE_ALL_ALARMS",
        "next_step": "ALARM",
        "show_confirmation": False,
        "params": {"enabled": enabled}
    }


def delete_all_alarms_node(state: AgentState):
    print("[System] 모든 알람 삭제 처리 중...")
    return {
        **state,
        "response_text": "모든 알람을 삭제했어요!",
        "action_required": "DELETE_ALL_ALARMS",
        "next_step": "ALARM",
        "show_confirmation": False,
        "params": {}
    }


def iot_action_node(state: AgentState):
    print("[System] IoT 이벤트 처리 중...")
    iot_data = state.get("iot_status", {})
    weight_change = iot_data.get("weight_change", 0)
    return {
        **state,
        "response_text": "약통에서 움직임이 감지됐어요! 방금 약 드셨나요?",
        "action_required": "SHOW_CONFIRMATION",
        "next_step": "NONE",
        "show_confirmation": True,
        "params": {
            "weight_change": weight_change,
            "detected_at": iot_data.get("timestamp", "")
        }
    }


def search_drug_node(state: AgentState):
    print("[System] 약 검색 처리 중...")
    messages = [
        SystemMessage(content="""사용자가 약을 검색하고 싶어합니다.
메시지에서 약 이름을 추출해서 keyword로 설정하세요."""),
        HumanMessage(content=f"사용자 메시지: {state['messages'][-1]}")
    ]
    try:
        ai_res = fast_structured.invoke(messages)
        return {**state, "response_text": ai_res.reply, "action_required": "SEARCH_DRUG",
                "next_step": "SEARCH_PILL", "show_confirmation": False, "params": ai_res.params.model_dump()}
    except Exception as e:
        return {**state, "response_text": "검색할 약 이름을 말씀해주세요!", "action_required": "NONE",
                "next_step": "NONE", "show_confirmation": False, "params": {}}


def write_post_node(state: AgentState):
    print("[System] 게시글 작성 처리 중...")
    messages = [
        SystemMessage(content="""게시글 초안을 작성해줘.
title: 짧고 명확하게, content: 2~3문장으로 간결하게, author: 없으면 "익명", board_type: free/med_question/review/notice
reply는 "게시글 초안 작성했어요! 확인해보세요 😊" 로 고정."""),
        HumanMessage(content=f"사용자 메시지: {state['messages'][-1]}")
    ]
    try:
        ai_res = fast_structured.invoke(messages)
        return {**state, "response_text": ai_res.reply, "action_required": "WRITE_POST",
                "next_step": "WRITE_BOARD", "show_confirmation": True, "params": ai_res.params.model_dump()}
    except Exception as e:
        return {**state, "response_text": "게시글 내용을 말씀해주세요!", "action_required": "NONE",
                "next_step": "NONE", "show_confirmation": False, "params": {}}

def post_submit_node(state: AgentState):
    print("[System] 게시글 등록 처리 중...")
    messages = [
        SystemMessage(content="""사용자가 게시글을 바로 등록하고 싶어합니다.
제목, 내용, 게시판 종류를 추출해서 등록 준비해주세요.
board_type: free/med_question/review/notice
reply는 "게시글을 등록할게요! 내용을 확인해주세요 😊" 로 고정."""),
        HumanMessage(content=f"사용자 메시지: {state['messages'][-1]}")
    ]
    try:
        ai_res = fast_structured.invoke(messages)
        return {**state, "response_text": ai_res.reply, "action_required": "WRITE_POST",
                "next_step": "WRITE_BOARD", "show_confirmation": False,
                "params": ai_res.params.model_dump()}
    except Exception as e:
        return {**state, "response_text": "게시글 내용을 말씀해주세요!", "action_required": "NONE",
                "next_step": "NONE", "show_confirmation": False, "params": {}}
    
def check_history_node(state: AgentState):
    print("[System] 복약 내역 확인 중...")

    iot_data = state.get("iot_status", {})
    pill_history = state.get("pill_history", [])

    # ✅ 조원 DB에서 실제 이력 가져오기
    try:
        res = requests.get(
            f"http://20.106.40.121/api/history/{state.get('user_id', 'User_01')}",
            timeout=3
        )
        db_history = res.json() if res.ok else []
        print(f"✅ DB 이력 {len(db_history)}개 조회 완료")
    except Exception as e:
        print(f"⚠️ DB 이력 조회 실패 (무시): {e}")
        db_history = []

    # ✅ 앱 메모리 + DB 이력 합산
    combined_history = pill_history + db_history

    m = "드셨어요" if iot_data.get("morning") else "안 드셨어요"
    l = "드셨어요" if iot_data.get("lunch") else "안 드셨어요"
    e = "드셨어요" if iot_data.get("evening") else "안 드셨어요"
    b = "드셨어요" if iot_data.get("bedtime") else "안 드셨어요"

    history_summary = f"아침 {m}, 점심 {l}, 저녁 {e}, 취침전 {b}"

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"""복약 내역 질문이에요.
오늘 현황: {history_summary}
기록 이력: {combined_history}
질문: {state['messages'][-1]}
이전 대화 맥락: {state.get('chat_history', [])[-2:]}
자연스럽게 답해주세요.""")
    ]

    try:
        ai_res = rich_structured.invoke(messages)
        return {**state, "response_text": ai_res.reply, "action_required": "NONE",
                "next_step": "NONE", "show_confirmation": False, "params": {}}
    except Exception as e:
        return {**state, "response_text": "복약 내역을 확인할 수 없어요!", "action_required": "NONE",
                "next_step": "NONE", "show_confirmation": False, "params": {}}


def drug_info_node(state: AgentState):
    """✅ 식약처 API + RAG 약 정보 제공"""
    print("[System] 약 정보 조회 중...")

    user_message = state["messages"][-1]

    # 약 이름 추출
    extract_messages = [
        SystemMessage(content="사용자 메시지에서 약 이름만 추출하세요. 약 이름만 답하세요. 없으면 '없음'이라고 답하세요."),
        HumanMessage(content=user_message)
    ]

    try:
        drug_name_response = llm.invoke(extract_messages)
        drug_name = drug_name_response.content.strip()
        print(f" -> 추출된 약 이름: {drug_name}")

        if drug_name == "없음" or not drug_name:
            return {**state, "response_text": "어떤 약에 대해 알고 싶으신가요?",
                    "action_required": "NONE", "next_step": "NONE",
                    "show_confirmation": False, "params": {}}

        drug_info = fetch_drug_info(drug_name)

        if not drug_info:
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=f"약 정보 질문: {user_message}\n식약처에서 해당 약을 찾지 못했어요. 알고 있는 정보로 답해주세요.")
            ]
        else:
            drug_context = f"""
[식약처 공식 약품 정보]
약품명: {drug_info.get('name', '')}
효능: {drug_info.get('effect', '정보 없음')}
사용법: {drug_info.get('usage', '정보 없음')}
주의사항: {drug_info.get('warning', '정보 없음')}
상호작용: {drug_info.get('interaction', '정보 없음')}
부작용: {drug_info.get('side_effect', '정보 없음')}
"""
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=f"""식약처 정보 기반으로 답해주세요.
{drug_context}
이전 대화: {state.get('chat_history', [])[-2:]}
질문: {user_message}
핵심만 요약해주세요.""")
            ]

        ai_res = rich_structured.invoke(messages)
        return {**state, "response_text": ai_res.reply, "action_required": "NONE",
                "next_step": "NONE", "show_confirmation": False, "params": {}}

    except Exception as e:
        print(f"(!) drug_info_node 실패: {e}")
        return {**state, "response_text": "약 정보를 가져오는 중 오류가 발생했어요!",
                "action_required": "NONE", "next_step": "NONE",
                "show_confirmation": False, "params": {}}
    
# =========================================================
# 복약 패턴 분석 함수
# =========================================================
def analyze_pill_pattern(pill_history: list) -> dict:
    """최근 복약 이력에서 평균 복약 시간 계산"""
    if not pill_history or len(pill_history) < 3:
        return {}  # 데이터 3개 이상부터 분석

    try:
        # 최근 5회 복약 시간 추출
        recent = [
            h for h in pill_history
            if h.get("taken") and h.get("time")
        ][-5:]

        if len(recent) < 3:
            return {}

        # 평균 시간 계산
        total_minutes = 0
        for record in recent:
            t = record["time"]  # "08:32" 형식
            h, m = map(int, t.split(":"))
            total_minutes += h * 60 + m

        avg_minutes = total_minutes // len(recent)
        avg_hour = avg_minutes // 60
        avg_min = avg_minutes % 60
        avg_time = f"{avg_hour:02d}:{avg_min:02d}"

        print(f"[패턴 분석] 최근 {len(recent)}회 평균 복약 시간: {avg_time}")

        return {
            "avg_time": avg_time,
            "sample_count": len(recent),
            "suggest": True
        }
    except Exception as e:
        print(f"(!) 패턴 분석 실패: {e}")
        return {}    


def chat_node(state: AgentState):
    print("[System] 일반 대화 처리 중...")

    # ✅ 메시지 없으면 바로 리턴
    if not state.get("messages"):
        return {**state, "response_text": "", "action_required": "NONE",
                "next_step": "IDLE", "show_confirmation": False, "params": {}}

    user_message = state['messages'][-1]
    if not user_message or not user_message.strip():
        return {**state, "response_text": "", "action_required": "NONE",
                "next_step": "IDLE", "show_confirmation": False, "params": {}}

    chat_history = state.get("chat_history", [])
    history_messages = []
    for msg in chat_history[-6:]:
        if msg["role"] == "user":
            history_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            history_messages.append(SystemMessage(content=f"[이전 매디 응답]: {msg['content']}"))

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        *history_messages,
        HumanMessage(content=f"현재 모드: {state.get('next_step')}\n데이터: {state['iot_status']}\n사용자: {state['messages'][-1]}")
    ]

    try:
        ai_res = rich_structured.invoke(messages)
        print(f" -> [매디 응답]: {ai_res.reply}")
        return {**state, "response_text": ai_res.reply, "action_required": ai_res.command,
                "next_step": ai_res.target, "show_confirmation": ai_res.show_confirmation,
                "params": ai_res.params.model_dump()}
    except Exception as e:
        print(f"(!) chat_node 실패: {e}")
        return {**state, "response_text": "잠시 후 다시 말씀해주세요!", "action_required": "NONE",
                "next_step": "IDLE", "show_confirmation": False, "params": {}}


# =========================================================
# 6. 라우팅 함수
# =========================================================
def route_by_intent(state: AgentState) -> str:
    intent = state.get("intent", "CHAT")
    print(f"[Router] 의도: {intent} → 해당 노드로 이동")
    routes = {
        "NAVIGATE": "navigate",
        "COMPLETE_DOSE": "complete_dose",
        "SET_ALARM": "set_alarm",
        "TOGGLE_ALL_ALARMS": "toggle_all_alarms",
        "DELETE_ALL_ALARMS": "delete_all_alarms",
        "IOT_EVENT": "iot_action",
        "SEARCH_DRUG": "search_drug",
        "WRITE_POST": "write_post",
        "CHECK_HISTORY": "check_history",
        "DRUG_INFO": "drug_info",
        "CHAT": "chat",
        "POST_SUBMIT": "post_submit"
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
workflow.add_node("toggle_all_alarms", toggle_all_alarms_node)
workflow.add_node("delete_all_alarms", delete_all_alarms_node)
workflow.add_node("iot_action", iot_action_node)
workflow.add_node("search_drug", search_drug_node)
workflow.add_node("write_post", write_post_node)
workflow.add_node("check_history", check_history_node)
workflow.add_node("drug_info", drug_info_node)
workflow.add_node("chat", chat_node)
workflow.add_node("post_submit", post_submit_node)

workflow.set_entry_point("monitor_iot")
workflow.add_edge("monitor_iot", "analyze_schedule")
workflow.add_edge("analyze_schedule", "classify_intent")
workflow.add_edge("post_submit", END)

workflow.add_conditional_edges(
    "classify_intent",
    route_by_intent,
    {
        "navigate": "navigate",
        "complete_dose": "complete_dose",
        "set_alarm": "set_alarm",
        "toggle_all_alarms": "toggle_all_alarms",
        "delete_all_alarms": "delete_all_alarms",
        "iot_action": "iot_action",
        "search_drug": "search_drug",
        "write_post": "write_post",
        "check_history": "check_history",
        "drug_info": "drug_info",
        "chat": "chat",
        "post_submit": "post_submit"
    }
)

for node in ["navigate", "complete_dose", "set_alarm", "toggle_all_alarms",
             "delete_all_alarms", "iot_action", "search_drug", "write_post",
             "check_history", "drug_info", "chat"]:
    workflow.add_edge(node, END)

app = workflow.compile()
print("✅ Maddy Agent 빌드 완료!")


# 8. 외부 전송 함수
def send_to_joone_fastapi(state: AgentState):
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
        response = requests.post(JOONE_API_URL, json=payload_data.model_dump(), timeout=5)
        if response.status_code == 200:
            print(f"✅ 전송 성공")
        else:
            print(f"❌ 전송 실패: {response.status_code}")
    except Exception as err:
        print(f"(!) 전송 오류: {err}")


def send_push_notification(user_id: str, title: str, body: str):
    PUSH_API_URL = "http://20.106.40.121/push/send"
    try:
        requests.post(PUSH_API_URL, json={"user_id": user_id, "title": title, "body": body, "priority": "high"}, timeout=3)
    except Exception as err:
        print(f"(!) Push 오류: {err}")


# 9. 메인 인터페이스 함수
def get_medie_response(
    user_message: str,
    current_mode: str,
    pill_history: list = [],
    chat_history: list = [],
    last_confirmed_timestamp: str = ""
):
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
        "params": {},
        "pill_history": pill_history,
        "chat_history": chat_history,
        "last_confirmed_timestamp": last_confirmed_timestamp,
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
        "params": final_result.get("params", {}),
        "pill_history": final_result.get("pill_history", pill_history),
        "last_confirmed_timestamp": final_result.get("last_confirmed_timestamp", last_confirmed_timestamp),
    }