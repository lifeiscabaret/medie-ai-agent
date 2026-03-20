import json
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from core.config import settings
from agent.prompts import SYSTEM_PROMPT

def get_medie_response(user_message: str, current_mode: str):
    llm = AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_deployment=settings.azure_openai_deployment_name,
        api_version=settings.azure_openai_api_version,
        api_key=settings.azure_openai_api_key,
        temperature=0.3 # 판단의 정확도를 위해 온도를 낮춤
    )

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"현재 화면: {current_mode}\n사용자: {user_message}")
    ]

    response = llm.invoke(messages)
    
    try:
        # LLM이 뱉은 JSON 문자열을 파싱
        result = json.loads(response.content)
        return result
    except:
        # 파싱 실패 시 대비 (폴백 로직)
        return {
            "reply": response.content,
            "command": "NONE",
            "target": "NONE"
        }