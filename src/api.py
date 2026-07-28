"""FastAPI service for Model Router — OpenAI-compatible API proxy."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .router import RouterEngine


engine: Optional[RouterEngine] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = RouterEngine()
    await engine.ensure_client()
    yield
    await engine.close()


app = FastAPI(title="Model Router", version="0.1.0", lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completion endpoint."""
    if not engine:
        raise HTTPException(503, "Router not initialized")

    body = await request.json()
    stream = body.get("stream", False)

    if stream:
        return StreamingResponse(
            engine.chat_completion_stream(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    else:
        result = await engine.chat_completion(body)
        return result


@app.get("/v1/models")
async def list_models():
    """List available models with their backends."""
    models = []
    for backend in engine._backends.values():
        for m in backend.models:
            models.append({
                "id": f"{backend.name}/{m}",
                "object": "model",
                "owned_by": backend.name,
                "backend": backend.name,
                "base_url": backend.base_url,
            })
    return {"object": "list", "data": models}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "backends": list(engine._backends.keys()),
        "model_count": sum(len(b.models) for b in engine._backends.values()),
    }
