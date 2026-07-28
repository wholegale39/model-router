"""Model Router — transparent proxy for multi-model routing.

Receives OpenAI-compatible requests, routes to the best backend
based on configured rules: task type, cost, availability, fallback.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class BackendConfig(BaseModel):
    """Configuration for a single model backend."""
    name: str                        # "deepseek", "openrouter", "openai"
    base_url: str                    # API endpoint
    api_key_env: str = "LLM_API_KEY" # Env var for API key
    models: list[str] = Field(default_factory=list)  # Models this backend serves
    priority: int = 10               # Lower = preferred
    max_retries: int = 2
    timeout_seconds: int = 120
    supports_stream: bool = True
    cost_per_1m_input: float = 0.0   # USD
    cost_per_1m_output: float = 0.0


class RouteRule(BaseModel):
    """Routing rule: match condition → target model."""
    name: str
    match_type: str = "model"        # "model", "prefix", "task", "always"
    match_value: str = ""            # e.g. "deepseek-chat", "deepseek/*", "cheap"
    target_model: str                # e.g. "deepseek/deepseek-chat"
    priority: int = 10


class RouterConfig(BaseModel):
    """Top-level configuration."""
    listen_host: str = "0.0.0.0"
    listen_port: int = 8771
    backends: list[BackendConfig] = Field(default_factory=list)
    rules: list[RouteRule] = Field(default_factory=list)
    default_model: str = "deepseek/deepseek-chat"
    log_level: str = "INFO"
    health_endpoint: bool = True


# ── Streaming helpers ─────────────────────────────────────

def parse_sse_line(line: str) -> Optional[dict]:
    """Parse a single SSE line like 'data: {...}'."""
    if line.startswith("data: "):
        import json
        data = line[6:].strip()
        if data == "[DONE]":
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return None


def build_chunk(model: str, content: str = "", finish_reason: Optional[str] = None) -> str:
    """Build an SSE chunk for streaming response."""
    import json
    chunk = {
        "id": f"chatcmpl-{__import__('uuid').uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(__import__('time').time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": finish_reason,
        }]
    }
    if content:
        chunk["choices"][0]["delta"]["content"] = content
    return f"data: {json.dumps(chunk)}\n\n"


def build_response(model: str, content: str, usage: Optional[dict] = None) -> dict:
    """Build a non-streaming response."""
    import time, uuid
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": usage or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    }
