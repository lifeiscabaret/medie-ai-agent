import os
import logging
from typing import Dict, Any
from dotenv import load_dotenv
from azure.cosmos import CosmosClient, exceptions

logger = logging.getLogger(__name__)
load_dotenv()

CONNECTION_STRING = os.getenv("COSMOS_CONNECTION_STRING")
if not CONNECTION_STRING:
    logger.error("🚨 환경 변수 COSMOS_CONNECTION_STRING이 설정되지 않았습니다!")

DATABASE_NAME = "MedieDB"
CONTAINER_NAME = "PillLogs"

def check_pill_weight_status(device_id: str, threshold: float = 2.0) -> Dict[str, Any]:
    try:
        client = CosmosClient.from_connection_string(CONNECTION_STRING)
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)

        query = "SELECT c.weight, c.timestamp FROM c WHERE c.deviceId = @device_id ORDER BY c.timestamp DESC OFFSET 0 LIMIT 2"
        parameters = [{"name": "@device_id", "value": device_id}]

        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        if not items or len(items) < 2:
            return {"status": "STABLE", "diff": 0, "message": "데이터가 충분하지 않아요, 멍!"}

        curr_weight = float(items[0].get("weight", 0.0))
        prev_weight = float(items[1].get("weight", 0.0))
        weight_diff = prev_weight - curr_weight

        if weight_diff >= threshold:
            logger.info(f"🚨 복용 의심 감지: {weight_diff}g 감소")
            return {
                "status": "DETECTED",
                "diff": round(weight_diff, 2),
                "message": f"무게가 {round(weight_diff, 2)}g 줄어들었어요. 약을 드신 것 같네요, 멍!"
            }

        return {"status": "STABLE", "diff": 0, "message": "상태가 안정적입니다, 멍!"}

    except exceptions.CosmosHttpResponseError as err:
        logger.error(f"Cosmos DB 쿼리 에러: {err.message}")
        return {"status": "ERROR", "message": "DB 연결에 문제가 생겼어요, 멍!"}
    except Exception as err:
        logger.error(f"Tool 에러 발생: {err}")
        return {"status": "ERROR", "message": str(err)}


def get_pill_weight_tool():
    from langchain.tools import Tool
    return Tool(
        name="check_pill_weight",
        func=check_pill_weight_status,
        description="사용자의 약통 무게 변화를 체크하여 약을 먹었는지 감지할 때 사용합니다."
    )