"""Core routing engine — resolves model names, manages backends, fallback chain."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Optional, AsyncGenerator

import httpx
from loguru import logger

from .models import BackendConfig, RouteRule, RouterConfig
from .models import build_chunk, build_response, parse_sse_line


class RouterEngine:
    """Routes chat completion requests to the best backend."""

    def __init__(self, config: Optional[RouterConfig] = None):
        self.config = config or RouterConfig()
        self._backends: dict[str, BackendConfig] = {}
        self._client: Optional[httpx.AsyncClient] = None
        self._load_defaults()

    def _load_defaults(self):
        """Load defaults from env if no config backends defined."""
        if not self.config.backends:
            self.config.backends = [
                BackendConfig(
                    name="deepseek",
                    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                    api_key_env="DEEPSEEK_API_KEY",
                    models=["deepseek-chat", "deepseek-reasoner"],
                    priority=1,
                    cost_per_1m_input=0.5,
                    cost_per_1m_output=2.0,
                ),
                BackendConfig(
                    name="openai",
                    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                    api_key_env="OPENAI_API_KEY",
                    models=["gpt-4o", "gpt-4o-mini", "gpt-4o-mini-*", "gpt-4*"],
                    priority=10,
                    cost_per_1m_input=2.5,
                    cost_per_1m_output=10.0,
                ),
                BackendConfig(
                    name="openrouter",
                    base_url="https://openrouter.ai/api/v1",
                    api_key_env="OPENROUTER_API_KEY",
                    models=["*"],  # Wildcard — catches everything else
                    priority=20,
                    cost_per_1m_input=0,
                    cost_per_1m_output=0,
                ),
            ]

        for b in self.config.backends:
            self._backends[b.name] = b

        if not self.config.rules:
            self.config.rules = [
                RouteRule(name="exact model", match_type="model",
                         match_value="deepseek-chat", target_model="deepseek/deepseek-chat"),
                RouteRule(name="openai models", match_type="prefix",
                         match_value="gpt-", target_model="openai/gpt-4o-mini"),
                RouteRule(name="cheap tasks", match_type="task",
                         match_value="cheap", target_model="openai/gpt-4o-mini"),
                RouteRule(name="fallback", match_type="always",
                         match_value="", target_model="openrouter/anthropic/claude-sonnet-4"),
            ]

    async def ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)

    async def close(self):
        if self._client:
            await self._client.aclose()

    # ── Model Resolution ──────────────────────────────

    def resolve(self, requested_model: str, task_type: str = "") -> tuple[BackendConfig, str]:
        """Resolve a requested model to (backend, full_model_name)."""
        # Try rules first
        for rule in sorted(self.config.rules, key=lambda r: r.priority):
            if self._rule_matches(rule, requested_model, task_type):
                backend_name, model_name = rule.target_model.split("/", 1)
                if backend_name in self._backends:
                    logger.info(f"Route: {requested_model} → {rule.target_model} (rule: {rule.name})")
                    return self._backends[backend_name], model_name

        # Direct backend lookup
        for backend in self._backends.values():
            for m in backend.models:
                if m == requested_model or m.endswith("*") and requested_model.startswith(m[:-1]):
                    logger.info(f"Route: {requested_model} → {backend.name}/{requested_model}")
                    return backend, requested_model

        # Fallback to default
        if "/" in self.config.default_model:
            parts = self.config.default_model.split("/", 1)
            default_backend = self._backends.get(parts[0])
            if default_backend:
                logger.warning(f"No rule matched {requested_model}, fallback to {self.config.default_model}")
                return default_backend, parts[1]

        # Last resort
        first = next(iter(self._backends.values()))
        logger.error(f"No backend found for {requested_model}, using {first.name}")
        return first, requested_model

    def _rule_matches(self, rule: RouteRule, model: str, task: str) -> bool:
        if rule.match_type == "model":
            return rule.match_value == model
        elif rule.match_type == "prefix":
            return model.startswith(rule.match_value)
        elif rule.match_type == "task":
            return rule.match_value == task
        elif rule.match_type == "always":
            return True
        return False

    # ── API Call ──────────────────────────────────────

    async def chat_completion(self, request: dict) -> dict:
        """Handle a non-streaming chat completion request."""
        model = request.get("model", "")
        task = request.get("_task_type", "")
        backend, resolved_model = self.resolve(model, task)

        # Build payload with resolved model
        payload = dict(request)
        payload["model"] = resolved_model
        payload.pop("_task_type", None)
        payload.pop("stream", None)

        last_error = None
        for attempt in range(backend.max_retries + 1):
            try:
                resp = await self._call_backend(backend, payload)
                return resp
            except Exception as e:
                last_error = e
                logger.warning(f"Backend {backend.name} attempt {attempt+1} failed: {e}")
                if attempt < backend.max_retries:
                    await asyncio.sleep(2 ** attempt)

        raise last_error or RuntimeError("All backends and retries exhausted")

    async def chat_completion_stream(self, request: dict) -> AsyncGenerator[str, None]:
        """Handle a streaming chat completion request, yielding SSE chunks."""
        model = request.get("model", "")
        task = request.get("_task_type", "")
        backend, resolved_model = self.resolve(model, task)

        payload = dict(request)
        payload["model"] = resolved_model
        payload.pop("_task_type", None)
        payload["stream"] = True

        last_error = None
        for attempt in range(backend.max_retries + 1):
            try:
                async for chunk in self._stream_backend(backend, payload):
                    yield chunk
                return  # Success
            except Exception as e:
                last_error = e
                logger.warning(f"Stream backend {backend.name} attempt {attempt+1} failed: {e}")
                if attempt < backend.max_retries:
                    await asyncio.sleep(2 ** attempt)

        # Final error as SSE
        yield build_chunk(resolved_model, "", "error")
        raise last_error or RuntimeError("Stream exhausted all retries")

    async def _call_backend(self, backend: BackendConfig, payload: dict) -> dict:
        """Make a non-streaming call to a backend."""
        await self.ensure_client()
        api_key = os.getenv(backend.api_key_env) or os.getenv("LLM_API_KEY")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        url = f"{backend.base_url.rstrip('/')}/chat/completions"
        logger.info(f"→ {backend.name} POST {url[:60]}...")

        resp = await self._client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        # Normalize model name in response
        data["model"] = f"{backend.name}/{data.get('model', payload['model'])}"
        logger.info(f"← {backend.name} {resp.status_code}")
        return data

    async def _stream_backend(self, backend: BackendConfig, payload: dict) -> AsyncGenerator[str, None]:
        """Make a streaming call to a backend, yielding SSE chunks."""
        await self.ensure_client()
        api_key = os.getenv(backend.api_key_env) or os.getenv("LLM_API_KEY")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        url = f"{backend.base_url.rstrip('/')}/chat/completions"
        logger.info(f"→ {backend.name} STREAM {url[:60]}...")

        async with self._client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                parsed = parse_sse_line(line)
                if parsed is None:  # [DONE]
                    yield build_chunk(payload["model"], "", "stop")
                    return
                # Pass through with modified model name
                parsed["model"] = f"{backend.name}/{parsed.get('model', payload['model'])}"
                yield f"data: {json.dumps(parsed)}\n\n"
