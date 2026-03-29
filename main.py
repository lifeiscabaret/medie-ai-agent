import threading
import time
from app.api import tts
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from datetime import datetime, timedelta, timezone

from agent.graph import app as medie_graph, get_medie_response, send_to_joone_fastapi, MedicationData

# ✅ Push Token 저장소
push_tokens = {}

# ✅ 알람 시간 저장소
alarm_times = {"User_01": "08:00"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    monitor_thread = threading.Thread(target=background_monitoring, daemon=True)
    monitor_thread.start()
    yield
    print("👋 [System] 서버 종료 중...")

app = FastAPI(title="Medie AI Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tts.router)


# ✅ Push 알림 전송 함수
def send_expo_push(user_id: str, title: str, body: str):
    token = push_tokens.get(user_id)
    if not token:
        print(f"⚠️ Push Token 없음: {user_id}")
        return
    try:
        requests.post("https://exp.host/--/api/v2/push/send", json={
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "priority": "high"
        }, timeout=5)
        print(f"✅ 푸시 알림 전송 완료: {user_id}")
    except Exception as e:
        print(f"⚠️ 푸시 알림 실패: {e}")


def background_monitoring():
    print("🚀 [System] 실시간 IoT 확인 스레드 시작")
    last_processed_time = ""
    last_alarm_sent = ""

    initial_state = {
        "user_id": "SYSTEM_MONITOR",
        "device_id": "Unknown",
        "iot_status": {},
        "schedule": [],
        "intent": "CHAT",
        "next_step": "IDLE",
        "action_required": "NONE",
        "response_text": "",
        "messages": [],
        "user_confirmed": False,
        "show_confirmation": False,
        "params": {},
        "pill_history": [],
        "chat_history": [],
        "last_confirmed_timestamp": "",
        "push_token": "",
    }

    while True:
        try:
            kst = timezone(timedelta(hours=9))
            now = datetime.now(kst)
            current_time = now.strftime('%H:%M')

            # ✅ 알람 시간 체크 → 푸시 알림
            for user_id, alarm_time in alarm_times.items():
                alarm_key = f"{user_id}_{alarm_time}_{now.strftime('%Y-%m-%d')}"
                if current_time == alarm_time and alarm_key != last_alarm_sent:
                    print(f"⏰ [Alarm] {user_id} 복약 시간 알림!")
                    send_expo_push(
                        user_id,
                        "💊 복약 시간이에요!",
                        "매디가 알려드려요. 지금 약 드실 시간이에요!"
                    )
                    last_alarm_sent = alarm_key

            # ✅ IoT 모니터링
            final_state = medie_graph.invoke(initial_state)
            iot_data = final_state.get("iot_status", {})
            current_iot_time = iot_data.get("timestamp", "") if isinstance(iot_data, dict) else ""

            if current_iot_time and current_iot_time != last_processed_time:
                print(f"🔔 [Background] 새 데이터 확인! 분석 중...")
                if final_state.get("next_step") != "IDLE":
                    send_to_joone_fastapi(final_state)
                last_processed_time = current_iot_time

        except Exception as err:
            print(f"⚠️ [Background Error]: {err}")

        time.sleep(30)


class ChatRequest(BaseModel):
    message: str
    current_mode: str
    user_id: str = "User_01"
    pill_history: list = []
    chat_history: list = []
    last_confirmed_timestamp: str = ""


class PushTokenRequest(BaseModel):
    user_id: str
    token: str

@app.post("/push-token")
async def save_push_token(req: PushTokenRequest):
    push_tokens[req.user_id] = req.token
    print(f"✅ Push Token 저장: {req.user_id} / {req.token[:20]}...")
    return {"status": "ok"}


class AlarmTimeRequest(BaseModel):
    user_id: str
    alarm_time: str

@app.post("/alarm-time")
async def save_alarm_time(req: AlarmTimeRequest):
    alarm_times[req.user_id] = req.alarm_time
    print(f"✅ 알람 시간 저장: {req.user_id} / {req.alarm_time}")
    return {"status": "ok"}

@app.get("/alarm-time/{user_id}")
async def get_alarm_time(user_id: str):
    alarm_time = alarm_times.get(user_id, "08:00")
    return {
        "morning": alarm_time,
        "lunch": "12:00",
        "evening": "18:00",
        "bedtime": "21:00"
    }


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    try:
        result = get_medie_response(
            req.message,
            req.current_mode,
            req.pill_history,
            req.chat_history,
            req.last_confirmed_timestamp
        )

        # ✅ LangGraph가 패턴 분석으로 SET_ALARM 제안하면 서버 alarm_times도 업데이트
        if result.get("command") == "SET_ALARM":
            new_time = result.get("params", {}).get("time")
            if new_time:
                alarm_times[req.user_id] = new_time
                print(f"✅ 패턴 분석 → 알람 시간 자동 업데이트: {req.user_id} / {new_time}")
                # ✅ 즉시 푸시 알림으로 알람 변경 알려주기
                send_expo_push(
                    req.user_id,
                    "⏰ 알람 시간이 변경됐어요!",
                    f"복약 패턴 분석 결과 {new_time}으로 알람을 맞춰드렸어요!"
                )

        return {
            "reply": result.get("reply"),
            "command": result.get("command"),
            "target": result.get("target"),
            "show_confirmation": result.get("show_confirmation", False),
            "params": result.get("params", {}),
            "pill_history": result.get("pill_history", []),
            "last_confirmed_timestamp": result.get("last_confirmed_timestamp", ""),
        }
    except Exception as e:
        print(f"❌ [Chat Error] {e}")
        return {
            "reply": "잠시 후 다시 말씀해주세요!",
            "command": "NONE",
            "target": "IDLE",
            "show_confirmation": False,
            "params": {}
        }


@app.post("/webhook/weight-log")
async def webhook_weight_log(data: MedicationData):
    print(f"🔔 [Webhook] 즉각 감지! 기기: {data.device_id}")

    # ✅ webhook으로 무게 감지되면 즉시 푸시 알림
    user_id = data.user_id if data.user_id != "Unknown" else "User_01"
    send_expo_push(
        user_id,
        "💊 약통 움직임 감지!",
        "방금 약 드셨나요? 매디에게 알려주세요!"
    )

    event_state = {
        "user_id": user_id,
        "device_id": data.device_id,
        "iot_status": data.model_dump(),
        "schedule": [],
        "intent": "IOT_EVENT",
        "next_step": "ANALYZING",
        "action_required": "NONE",
        "response_text": "",
        "messages": [],
        "user_confirmed": False,
        "show_confirmation": False,
        "params": {},
        "pill_history": [],
        "chat_history": [],
        "last_confirmed_timestamp": "",
        "push_token": push_tokens.get(user_id, ""),  # ✅ push_token 전달
    }

    final_result = medie_graph.invoke(event_state)

    if final_result.get("next_step") != "IDLE":
        send_to_joone_fastapi(final_result)

    return {"status": "success", "msg": "무게 분석 완료"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)