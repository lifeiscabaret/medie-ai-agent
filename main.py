import threading
import time
from app.api import tts
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.graph import app as medie_graph, get_medie_response, send_to_joone_fastapi, MedicationData

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

def background_monitoring():
    print("🚀 [System] 실시간 IoT 확인 스레드 시작")
    last_processed_time = ""

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
        "params": {}
    }

    while True:
        try:
            final_state = medie_graph.invoke(initial_state)
            iot_data = final_state.get("iot_status", {})
            current_time = iot_data.get("timestamp", "") if isinstance(iot_data, dict) else ""

            if current_time and current_time != last_processed_time:
                print(f"🔔 [Background] 새 데이터 확인! 분석 중...")
                if final_state.get("next_step") != "IDLE":
                    send_to_joone_fastapi(final_state)
                last_processed_time = current_time
        except Exception as err:
            print(f"⚠️ [Background Error]: {err}")

        time.sleep(30)


class ChatRequest(BaseModel):
    message: str
    current_mode: str
    user_id: str = "User_01"

@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """매디 챗 엔드포인트"""
    try:
        result = get_medie_response(req.message, req.current_mode)
        return {
            "reply": result.get("reply"),
            "command": result.get("command"),
            "target": result.get("target"),
            "show_confirmation": result.get("show_confirmation", False),
            "params": result.get("params", {})
        }
    except Exception as e:
        print(f"❌ [Chat Error] {e}")
        return {
            "reply": "멍! 대답하기가 조금 힘들다멍...",
            "command": "NONE",
            "target": "IDLE",
            "show_confirmation": False,
            "params": {}
        }


@app.post("/webhook/weight-log")
async def webhook_weight_log(data: MedicationData):
    """IoT 즉각 감지 Webhook"""
    print(f"🔔 [Webhook] 즉각 감지! 기기: {data.device_id}")

    event_state = {
        "user_id": "SYSTEM_TRIGGER",
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
        "params": {}
    }

    final_result = medie_graph.invoke(event_state)

    if final_result.get("next_step") != "IDLE":
        send_to_joone_fastapi(final_result)

    return {"status": "success", "msg": "무게 분석 완료"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)