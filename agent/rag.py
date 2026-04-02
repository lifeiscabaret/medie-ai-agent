import logging
import urllib.parse
from typing import Optional

import requests
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import AzureOpenAIEmbeddings

from core.config import settings

logger = logging.getLogger(__name__)

DRUG_API_MAX_RESULTS = 5
RAG_SEARCH_RESULTS = 3

embeddings = AzureOpenAIEmbeddings(
    azure_endpoint=settings.azure_openai_endpoint,
    azure_deployment="text-embedding-ada-002",
    api_version=settings.azure_openai_api_version,
    api_key=settings.azure_openai_api_key,
)


def get_vectorstore() -> Chroma:
    return Chroma(
        persist_directory=settings.chroma_path,
        embedding_function=embeddings
    )


def fetch_and_store_drug(drug_name: str) -> bool:
    """식약처 API 호출 → ChromaDB 저장"""
    try:
        params = {
            "serviceKey": settings.drug_api_key,
            "itemName": drug_name,
            "pageNo": "1",
            "numOfRows": str(DRUG_API_MAX_RESULTS),
            "type": "json"
        }
        url = f"{settings.drug_api_endpoint}?{urllib.parse.urlencode(params)}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()

        items = response.json().get("body", {}).get("items", [])
        if not items:
            logger.info(f"식약처 검색 결과 없음: {drug_name}")
            return False

        docs = [
            Document(
                page_content="\n".join([
                    f"약품명: {item.get('itemName', '')}",
                    f"효능: {item.get('efcyQesitm', '')}",
                    f"사용법: {item.get('useMethodQesitm', '')}",
                    f"주의사항: {item.get('atpnWarnQesitm', '')}",
                    f"부작용: {item.get('seQesitm', '')}",
                    f"상호작용: {item.get('intrcQesitm', '')}",
                ]).strip(),
                metadata={
                    "drug_name": item.get("itemName", ""),
                    "source": "식약처"
                }
            )
            for item in items
        ]

        vectorstore = get_vectorstore()
        vectorstore.add_documents(docs)
        logger.info(f"ChromaDB 저장 완료: {drug_name} ({len(docs)}건)")
        return True

    except requests.RequestException as e:
        logger.error(f"식약처 API 요청 실패: {e}")
        return False
    except Exception as e:
        logger.error(f"ChromaDB 저장 실패: {e}", exc_info=True)
        return False


def search_drug_from_rag(query: str, k: int = RAG_SEARCH_RESULTS) -> Optional[str]:
    """ChromaDB에서 유사 약품 정보 검색"""
    try:
        vectorstore = get_vectorstore()

        if vectorstore._collection.count() == 0:
            logger.debug("ChromaDB 비어있음, RAG 스킵")
            return None

        docs = vectorstore.similarity_search(query, k=k)
        if not docs:
            return None

        logger.info(f"RAG 검색 성공: {query} ({len(docs)}건)")
        return "\n\n".join([doc.page_content for doc in docs])

    except Exception as e:
        logger.error(f"RAG 검색 실패: {e}", exc_info=True)
        return None