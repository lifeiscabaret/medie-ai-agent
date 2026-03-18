# main.py
from fastapi import FastAPI
from pydantic import BaseModel
from agent.graph import get_medie_response

app = FastAPI(title="Medie AI Agent")

class ChatRequest(BaseModel):
    message: str
    current_mode: str

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    # Medie의 뇌를 가동하여 응답 생성
    result = get_medie_response(req.message, req.current_mode)
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)