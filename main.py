import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.graph import app as medie_graph, get_medie_response, send_to_joone_fastapi

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 시작 시 모니터링 스레드 실행
    monitor_thread = threading.Thread(target=background_monitoring, daemon=True)
    monitor_thread.start()
    yield
    print("👋 [System] 서버 종료 중...")

app = FastAPI(title="Medie AI Agent", lifespan=lifespan)

# CORS 설정 (React Native 연결 필수)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def background_monitoring():
    print("🚀 [System] 실시간 IoT 확인 스레드가 시작되었습니다.")
    last_processed_time = ""

    initial_state = {
        "user_id": "SYSTEM_MONITOR",
        "device_id": "Unknown",
        "iot_status": {},
        "schedule": [],
        "next_step": "IDLE",
        "action_required": "NONE",
        "response_text": "",
        "messages": [],
        "user_confirmed": False
    }

    while True:
        try:
            # 수정: app -> medie_graph
            final_state = medie_graph.invoke(initial_state)
            
            # iot_status가 dict가 아닐 경우를 대비한 안전장치
            iot_data = final_state.get("iot_status", {})
            current_time = iot_data.get("timestamp", "") if isinstance(iot_data, dict) else ""

            if current_time and current_time != last_processed_time:
                print(f"🔔 [Background] 새 데이터 확인! 분석 중...")
                if final_state.get("next_step") != "IDLE":
                    send_to_joone_fastapi(final_state)
                last_processed_time = current_time
        except Exception as err:
            print(f"⚠️ [Background Error] 확인 중 오류 발생: {err}")
        
        time.sleep(30)

class ChatRequest(BaseModel):
    message: str
    current_mode: str
    user_id: str = "User_01" # [추가] 유저 아이디 기본값

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """매디야~ 라고 불렀을 때 실행되는 메인 엔진"""
    try:
        # get_medie_response가 딕셔너리를 반환하는지, Pydantic 객체를 반환하는지 확인 필요
        result = get_medie_response(req.message, req.current_mode)
        
        # 객체일 경우를 대비해 안전하게 처리
        if hasattr(result, "dict"): # Pydantic v1
            res_dict = result.dict()
        elif hasattr(result, "model_dump"): # Pydantic v2
            res_dict = result.model_dump()
        else:
            res_dict = result # 이미 dict인 경우

        return {
            "reply": res_dict.get("response_text") or res_dict.get("reply"),
            "command": res_dict.get("action_required") or res_dict.get("command"),
            "target": res_dict.get("next_step") or res_dict.get("target")
        }
    except Exception as e:
        print(f"❌ [Chat Error] {e}")
        return {"reply": "멍! 대답하기가 조금 힘들다멍...", "command": "NONE", "target": "IDLE"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)