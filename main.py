# main.py
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from agent.graph import get_medie_response, app, send_to_joone_fastapi

# 2. 🔥 Lifespan 핸들러 정의 (on_event 대체)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # [Startup] 서버가 시작될 때 실행될 로직
    monitor_thread = threading.Thread(target=background_monitoring, daemon=True)
    monitor_thread.start()
    
    yield  # 서버가 돌아가는 지점
    
    # [Shutdown] 서버가 종료될 때 실행될 로직 (필요 시)
    print("👋 [System] 서버 종료 중... 서버 확인 스레드도 함께 종료됩니다.")

# app에서 fastapi_app으로 수정한 이유가 graph.py의 app과 이름이 겹쳐서 수정하였음
fastapi_app = FastAPI(title="Medie AI Agent", lifespan=lifespan)

#----
# 백그라운드 실시간 확인 로직 추가
#----
def background_monitoring():
    """서버가 돌아가는 동안 별도의 길에서 스토리지를 확인합니다."""
    print("🚀 [System] 실시간 IoT 확인 스레드가 시작되었습니다.")
    last_processed_time = ""

    # 에이전트 초기 상태
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
            # 30초마다 한 번씩 graph를 실행
            final_state = app.invoke(initial_state)
            current_time = final_state["iot_status"].get("timestamp", "")

            if current_time and current_time != last_processed_time:
                print(f"🔔 [Background] 새 데이터 확인! 분석 중...")
                if final_state["next_step"] != "IDLE":
                    send_to_joone_fastapi(final_state)
                last_processed_time = current_time
        except Exception as err:
            print(f"⚠️ [Background Error] 확인 중 오류 발생: {err}")
        
        time.sleep(30) # 30초 대기

# 앱에서 보낼 요청 형식
class ChatRequest(BaseModel):
    message: str
    current_mode: str

@fastapi_app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    # Medie의 뇌를 가동하여 응답 생성
    result = get_medie_response(req.message, req.current_mode)
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)