from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment_name: str
    azure_openai_api_version: str  
    
    # 🔴 아래 두 줄을 꼭 추가해 주세요! (에러 로그에서 찾던 이름들입니다)
    cosmos_connection_string: str
    jwt_secret_key: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    
settings = Settings()