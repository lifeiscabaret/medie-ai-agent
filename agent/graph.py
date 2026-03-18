from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from core.config import settings
from agent.prompts import SYSTEM_PROMPT

def get_medie_response(user_message: str, current_mode: str):
    # 1. Azure OpenAI 연결
    llm = AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_deployment=settings.azure_openai_deployment_name,
        api_version=settings.openai_api_version,
        api_key=settings.azure_openai_api_key,
        temperature=0.7
    )

    # 2. 메시지 구성
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"현재 앱 화면: {current_mode}\n사용자 메시지: {user_message}")
    ]

    # 3. AI 추론
    response = llm.invoke(messages)
    content = response.content

    # 4. 단순 화면 전환 판단 (나중에 Tool 사용으로 업그레이드 가능)
    target = "NONE"
    command = "NONE"
    
    if "MAP" in content or "지도" in content:
        target = "MAP"
        command = "MOVE_SCREEN"
    elif "SCAN" in content or "사진" in content:
        target = "SCAN"
        command = "MOVE_SCREEN"

    return {
        "reply": content,
        "command": command,
        "target": target
    }