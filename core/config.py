from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment_name: str
    azure_openai_api_version: str

    azure_storage_connection_string: str
    cosmos_connection_string: str
    jwt_secret_key: str

    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_model_id: str = "eleven_multilingual_v2"

    drug_api_key: str | None = None
    drug_api_endpoint: str | None = None

    # 백엔드
    backend_url: str = "http://localhost:8000"
    default_user_id: str = "User_01"

    # RAG
    chroma_path: str = "./chroma_db"
    chroma_collection_size: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()