import os
import logging
from typing import Dict, Any
from dotenv import load_dotenv
from azure.cosmos import CosmosClient, exceptions

# 로깅 설정
logger = logging.getLogger(__name__)

# .env 파일 불러오기
load_dotenv()

# 환경 변수에서 DB 연결 문자열 가져오기
CONNECTION_STRING = os.getenv("COSMOS_CONNECTION_STRING")

# 만약 .env 파일을 못 찾았을 경우 에러 방지
if not CONNECTION_STRING:
    logger.error("🚨 환경 변수 COSMOS_CONNECTION_STRING이 설정되지 않았습니다!")

# DB/컨테이너 이름 설정 (권혁님께 물어보고 정확한 이름으로 바꿔주세요!)
DATABASE_NAME = "MedieDB"   # 예시: 데이터베이스 이름
CONTAINER_NAME = "PillLogs" # 예시: 컨테이너(테이블) 이름



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


        # 1. Cosmos DB 클라이언트 연결
        client = CosmosClient.from_connection_string(CONNECTION_STRING)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)

        # 2. 쿼리 작성: 해당 기기(device_id)의 최신 데이터 딱 2개만 가져오기 (시간 역순)
        # (아두이노에서 보낸 JSON 필드명에 맞춰 c.weight, c.timestamp, c.deviceId 등을 맞춰야 합니다)
        query = "SELECT c.weight, c.timestamp FROM c WHERE c.deviceId = @device_id ORDER BY c.timestamp DESC OFFSET 0 LIMIT 2"
        parameters = [{"name": "@device_id", "value": device_id}]
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        # 3. 데이터 검증 (데이터가 2개 미만이면 비교 불가)
        if not items or len(items) < 2:
            return {"status": "STABLE", "diff": 0, "message": "데이터가 충분하지 않아요, 멍!"}

        # 주의: ORDER BY DESC(내림차순)로 가져왔으므로 
        # items[0]이 방금 들어온 최신 데이터, items[1]이 그 이전 데이터입니다.
        curr_weight = float(items[0].get("weight", 0.0))
        prev_weight = float(items[1].get("weight", 0.0))
        weight_diff = prev_weight - curr_weight

        # 4. 무게 비교 로직 (무게가 줄어든 경우 = 복용 의심)
        if weight_diff >= threshold:
            logger.info(f"🚨 복용 의심 감지: {weight_diff}g 감소")
            return {
                "status": "DETECTED",
                "diff": round(weight_diff, 2),
                "message": f"무게가 {round(weight_diff, 2)}g 줄어들었어요. 약을 드신 것 같네요, 멍!"
            }
        
        # 무게 변화가 없거나 늘어난 경우 (무시)
        return {
            "status": "STABLE",
            "diff": round(weight_diff, 2),
            "message": "상태가 안정적입니다, 멍!"
        }

    except exceptions.CosmosHttpResponseError as err:
        logger.error(f"Cosmos DB 쿼리 에러: {err.message}")
        return {"status": "ERROR", "message": "DB 연결에 문제가 생겼어요, 멍!"}
    except Exception as err:
        logger.error(f"Tool 에러 발생: {err}")
        return {"status": "ERROR", "message": str(err)}

# LangChain Tool로 등록하기 위한 함수
def get_pill_weight_tool():
    from langchain.tools import Tool
    return Tool(
        name="check_pill_weight",
        func=check_pill_weight_status,
        description="사용자의 약통 무게 변화를 체크하여 약을 먹었는지 감지할 때 사용합니다."
    )