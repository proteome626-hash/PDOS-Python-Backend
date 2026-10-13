"""routers/chat_router.py — Proxy to NVIDIA API (moonshotai/kimi-k3) with SSE streaming."""
import os
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse, JSONResponse
import auth, models
from routers.deps import get_text, limiter

router = APIRouter()

NVIDIA_API_KEY = os.environ.get("Bearer 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", "")
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


@router.get("/")
def root():
    return {"status": "online", "model": "moonshotai/kimi-k3"}


@router.post("/v1/chat/completions")
@limiter.limit("10/minute")
async def chat_completions(
    request: Request,
    data: dict,
    current_user: models.User = Depends(auth.require_password_set),
):
    if not NVIDIA_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"error": get_text("api_key_not_configured", request)},
        )

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }

    stream = data.get("stream", False)

    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            NVIDIA_URL,
            headers=headers,
            json=data,
            stream=stream,
        )

        if response.status_code != 200:
            return JSONResponse(
                status_code=response.status_code,
                content=response.json(),
            )

        if stream:
            return StreamingResponse(
                _stream_generator(response),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        return response.json()


async def _stream_generator(response: httpx.Response):
    try:
        async for chunk in response.aiter_text():
            if chunk.strip():
                yield chunk
    finally:
        await response.aclose()