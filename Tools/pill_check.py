import logging
from typing import Dict, Any

# 로깅 설정
logger = logging.getLogger(__name__)

def check_pill_weight_status(device_id: str, threshold: float = 2.0) -> Dict[str, Any]:
    """
    Cosmos DB의 최신 무게 데이터를 비교하여 복용 의심 상황 감지.
    
    Args:
        device_id: 약통 기기 고유 ID
        threshold: 복용으로 간주할 최소 무게 변화량 (기본 2g)
        
    Returns:
        Dict: 감지 상태, 변화량, 메시지 포함
    """
    try:
        # 실제 환경에서는 권혁님이 만든 
        # get_latest_device_logs(device_id) 함수를 호출하게 됩니다.
        
        # [백엔드 연결 전 테스트용 목업 데이터]
        # 시나리오: 이전 무게 100g -> 현재 무게 95g (5g 감소)
        mock_logs = [
            {"weight": 100.0, "timestamp": "2026-03-20T10:00:00Z"},
            {"weight": 95.0, "timestamp": "2026-03-20T10:05:00Z"}
        ]
        
        if not mock_logs or len(mock_logs) < 2:
            return {"status": "STABLE", "diff": 0, "message": "데이터가 충분하지 않아요, 멍!"}

        prev_weight = mock_logs[0]["weight"]
        curr_weight = mock_logs[-1]["weight"]
        weight_diff = prev_weight - curr_weight

        # 무게가 줄어든 경우 (복용 의심)
        if weight_diff >= threshold:
            logger.info(f"🚨 복용 감지: {weight_diff}g 감소")
            return {
                "status": "DETECTED",
                "diff": round(weight_diff, 2),
                "message": "무게 변화가 감지되었습니다. 확인이 필요합니다!"
            }
        
        # 무게 변화가 없거나 늘어난 경우 (무시)
        return {
            "status": "STABLE",
            "diff": round(weight_diff, 2),
            "message": "상태가 안정적입니다, 멍!"
        }

    except Exception as e:
        logger.error(f"Tool 에러 발생: {e}")
        return {"status": "ERROR", "message": str(e)}

# LangChain Tool로 등록하기 위한 함수 (나중에 사용)
def get_pill_weight_tool():
    from langchain.tools import Tool
    return Tool(
        name="check_pill_weight",
        func=check_pill_weight_status,
        description="사용자의 약통 무게 변화를 체크하여 약을 먹었는지 감지할 때 사용합니다."
    )