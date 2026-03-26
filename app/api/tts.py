import os
from io import BytesIO

import httpx
from core.config import settings

ELEVENLABS_API_KEY = settings.elevenlabs_api_key
ELEVENLABS_VOICE_ID = settings.elevenlabs_voice_id
ELEVENLABS_MODEL_ID = settings.elevenlabs_model_id
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    voice_id: str | None = None
    model_id: str | None = None


@router.post("/tts")
async def text_to_speech(payload: TTSRequest):
    if not ELEVENLABS_API_KEY:
        raise HTTPException(status_code=500, detail="ELEVENLABS_API_KEY가 설정되지 않았어.")
    if not ELEVENLABS_VOICE_ID and not payload.voice_id:
        raise HTTPException(status_code=500, detail="voice_id가 설정되지 않았어.")

    voice_id = payload.voice_id or ELEVENLABS_VOICE_ID
    model_id = payload.model_id or ELEVENLABS_MODEL_ID

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    body = {
        "text": payload.text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.72,
            "similarity_boost": 0.88,
            "style": 0.18,
            "use_speaker_boost": True,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"ElevenLabs 호출 실패: {response.text}",
            )
        
        print("voice_id:", repr(voice_id))
        print("model_id:", repr(model_id))
        print("url:", repr(url))

        audio_bytes = BytesIO(response.content)

        return StreamingResponse(
            audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": 'inline; filename="mediemung.mp3"'
            },
        )

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"TTS 네트워크 오류: {str(e)}")
    
