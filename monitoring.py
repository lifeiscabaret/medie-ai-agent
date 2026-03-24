# monitoring.py
import time
# 만든 app(그래프)과 전송 함수를 가져옵니다.
from agent.graph import app, monitor_iot_node, send_to_joone_fastapi

def start_monitoring():
    print("\n" + "="*50)
    print("🐶 매디 복약 알람 자동화 시스템 가동!")
    print("="*50)

    # 1. 초기 상태 설정 (루프 밖에서 한 번만 정의)
    # 이 initial_state는 에이전트가 동작하기 위한 '기본 메모리'
    initial_state = {
        "user_id": "User_01",
        "device_id": "Unknown",
        "iot_status": {},
        "schedule": [],
        "next_step": "IDLE",
        "action_required": "NONE",
        "response_text": "",
        "messages": [],
        "user_confirmed": False
    }

    # 마지막으로 확인한 데이터의 타임스탬프를 저장 (중복 전송 방지용 변수)
    last_processed_time = ""

    # 마지막으로 확인한 데이터의 타임스탬프를 저장 (중복 전송 방지용 변수)
    last_processed_time = ""

    print("=== [시스템 체크] Azure Storage 연결 확인 ===")
    # 2. 첫 실행 시 연결이 잘 되는지 한 번 테스트
    test_check = monitor_iot_node(initial_state)
    
    # 3. 결과 확인
    print("\n=== 테스트 결과 ===")
    if test_check["iot_status"]:
        print(f"✅ 성공: 데이터를 가져왔습니다.")
        print(f"   - 기기 ID: {test_check['device_id']}")
        print(f"   - 전체 데이터: {test_check['iot_status']}")
    else:
        print("❌ 실패: 데이터를 가져오지 못했습니다. 출력된 에러 로그를 확인하세요.")
    
    try:
        while True:
            print("\n" + "-"*30)
            print(f"[서버 확인 루프 체크중...] 현재 시각: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 1단계: 에이전트 실행 (스토리지 확인 -> 스케줄 대조 -> LLM 추론)
            # app.invoke를 실행하면 정의한 3개의 노드가 순차적으로 돌아갑니다.
            try:
                final_state = app.invoke(initial_state)
            except Exception as e:
                print(f"❌ 에이전트 실행 중 오류 발생: {e}")
                time.sleep(10)
                continue

            # 2단계: 새로운 데이터인지 확인 (타임스탬프 비교)
            # 아두이노에서 보낸 데이터에 timestamp 필드가 있다고 가정
            current_data_time = final_state["iot_status"].get("timestamp", "")
            
            # 데이터가 있고, 이전 데이터와 다를 때만 전송
            if current_data_time and current_data_time != last_processed_time:
                print(f"✨ 새 데이터 감지! 분석 결과: {final_state['next_step']}")
                print(f"🐶 매디의 한마디: {final_state['response_text']}")
                
                # 3단계: 분석 결과가 의미 있는 경우(알람 등) 조원 서버로 전송
                if final_state["next_step"] != "IDLE":
                    send_to_joone_fastapi(final_state)
                
                # 확인 완료된 타임스탬프 업데이트
                last_processed_time = current_data_time

            else:
                print("😴 새로운 무게 변화가 없거나 데이터가 동일합니다. 대기 중...")

            # 4. 4단계: 확인 주기 설정 (예: 30초마다 한 번씩 스토리지 체크)
            time.sleep(30)

    except KeyboardInterrupt:
        print("\n\n👋 시스템을 안전하게 종료합니다. 다음에 봐요 멍!")

if __name__ == "__main__":
    start_monitoring()