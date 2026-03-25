# main.py
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from agent.graph import app, get_medie_response, MedicationData, send_to_joone_fastapi
from agent.monitoring import start_monitoring

# 2. 🔥 Lifespan 핸들러 정의 (on_event 대체)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 백업용으로 기존 모니터링(폴링)도 돌려둡니다 (안전장치)
    # [Startup] 서버가 시작될 때 실행될 로직 및 백그라운드 실시간 확인 로직
    monitor_thread = threading.Thread(target=start_monitoring, daemon=True)
    monitor_thread.start()
    
    yield  # 서버가 돌아가는 지점
    
    # [Shutdown] 서버가 종료될 때 실행될 로직 (필요 시)
    print("👋 [System] 서버 종료 중... 서버 확인 스레드도 함께 종료됩니다.")

# app에서 fastapi_app으로 수정한 이유가 graph.py의 app과 이름이 겹쳐서 수정하였음
fastapi_app = FastAPI(title="Medie AI Agent", lifespan=lifespan)

# --- [추가된 부분: 실시간 감지 Webhook] ---

@fastapi_app.post("/webhook/weight-log")
async def trigger_agent(data: MedicationData): # 2. graph.py에서 가져온 스키마 사용!
    """
    Weight Logs 테이블에 새 데이터가 쌓이면 즉각 호출되는 문입니다.
    """
    print(f"🔔 [Webhook] 즉각 감지! 분석을 시작합니다. (기기: {data.device_id})")

    # 3. Webhook으로 받은 데이터를 에이전트 상태에 바로 주입
    event_state = {
        "user_id": "SYSTEM_TRIGGER",
        "device_id": data.device_id,
        "iot_status": data.model_dump(), # Pydantic 모델을 딕셔너리로 변환
        "schedule": [],
        "next_step": "ANALYZING", 
        "action_required": "NONE",
        "response_text": "",
        "messages": [],
        "user_confirmed": False
    }

    # 4. 루프 기다리지 않고 즉시 실행!
    final_result = app.invoke(event_state)
    
    # 5. 결과가 나오면 DB 서버로 바로 쏘도록 구현
    if final_result["next_step"] != "IDLE":
        send_to_joone_fastapi(final_result)

    return {"status": "success", "msg": "무게 분석 완료"}


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