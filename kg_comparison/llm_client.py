"""Shared OpenAI-compatible LLM client with TokenUsage telemetry and exact request caching."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.core.llm_config import LLMSettings  # noqa: E402

from kg_comparison.schema import CaseUsage  # noqa: E402

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_json_content(content: str) -> dict[str, Any]:
    """Parse a JSON object from model content, tolerating markdown code fences."""
    stripped = content.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = _FENCED_JSON_RE.search(stripped)
        if not match:
            raise
        parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object from LLM, got {type(parsed).__name__}")
    return parsed


class DiskResponseCache:
    """Exact request-level disk cache for reproducible baseline experiments."""

    def __init__(self, directory: Path, *, cache_only: bool = False) -> None:
        self.directory = directory
        self.cache_only = cache_only
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(
        self,
        *,
        namespace: str,
        endpoint_hash: str,
        request_body: dict[str, Any],
        compute: Any,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        identity = {
            "namespace": namespace,
            "endpoint_hash": endpoint_hash,
            "request": request_body,
        }
        key = _stable_hash(identity)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            path = self.directory / f"{key}.json"
            if path.exists():
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if envelope.get("response_hash") != _stable_hash(envelope.get("response")):
                    raise ValueError(f"缓存校验失败: {path}")
                return envelope["response"], envelope.get("usage"), True
            if self.cache_only:
                raise KeyError(
                    f"cache-only 模式下未命中缓存请求: namespace={namespace} key={key[:12]}"
                )
            response, usage = compute()
            envelope = {
                **identity,
                "response": response,
                "usage": usage,
                "response_hash": _stable_hash(response),
                "created_at": datetime.now(UTC).isoformat(),
            }
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.directory,
                    prefix=".cmp-cache-",
                    delete=False,
                ) as handle:
                    tmp_path = Path(handle.name)
                    json.dump(envelope, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(tmp_path, path)
                except FileExistsError:
                    pass
            finally:
                if tmp_path:
                    tmp_path.unlink(missing_ok=True)
            return response, usage, False


class ComparisonLLMClient:
    """OpenAI-compatible JSON chat client aligned with RAG_drift model configuration."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        timeout_seconds: float = 120.0,
        max_tokens: int = 8192,
        reasoning_effort: str = "none",
        cache: DiskResponseCache | None = None,
    ) -> None:
        settings = LLMSettings()
        self.base_url = (base_url or settings.base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.cache = cache
        self.endpoint_hash = _stable_hash([self.base_url, self.api_key or ""])

    def _build_body(self, system: str, user: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.model.lower().startswith("deepseek"):
            body["thinking"] = {
                "type": "disabled" if self.reasoning_effort == "none" else "enabled"
            }
            body["reasoning_effort"] = self.reasoning_effort
        return body

    def _call_live(self, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if not self.api_key:
            raise ValueError("缺少 LLM_API_KEY，请在 .env 或环境变量中配置后运行")
        attempts = 3
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                envelope = response.json()
                content = envelope["choices"][0]["message"]["content"]
                parsed = parse_json_content(content)
                usage = envelope.get("usage")
                return parsed, usage if isinstance(usage, dict) else None
            except Exception as exc:
                last_exc = exc
                retryable = isinstance(exc, httpx.TransportError | json.JSONDecodeError) or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and (exc.response.status_code == 429 or exc.response.status_code >= 500)
                )
                if retryable and attempt + 1 < attempts:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"LLM 请求或 JSON 解析失败 ({type(exc).__name__}): {exc}"
                ) from exc
        raise RuntimeError(f"LLM 请求重试耗尽: {last_exc}")

    def request_json(
        self,
        *,
        system: str,
        user: str,
        stage: str,
        namespace: str,
        usage_acc: CaseUsage,
    ) -> dict[str, Any]:
        """Execute a structured JSON call and accumulate token/call stats into usage_acc."""
        body = self._build_body(system, user)
        if self.cache is not None:
            parsed, raw_usage, replayed = self.cache.get(
                namespace=namespace,
                endpoint_hash=self.endpoint_hash,
                request_body=body,
                compute=lambda: self._call_live(body),
            )
        else:
            parsed, raw_usage = self._call_live(body)
            replayed = False

        stage_bucket = usage_acc.by_stage.setdefault(
            stage,
            {
                "http_calls": 0,
                "replayed_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        )
        if replayed:
            usage_acc.replayed_calls += 1
            stage_bucket["replayed_calls"] += 1
        else:
            usage_acc.http_calls += 1
            stage_bucket["http_calls"] += 1

        # Record provider usage when available (including cached usage for cost reporting)
        if isinstance(raw_usage, dict) and all(
            isinstance(raw_usage.get(k), int) and raw_usage[k] >= 0
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            pt = int(raw_usage["prompt_tokens"])
            ct = int(raw_usage["completion_tokens"])
            tt = int(raw_usage["total_tokens"])
            usage_acc.prompt_tokens += pt
            usage_acc.completion_tokens += ct
            usage_acc.total_tokens += tt
            stage_bucket["prompt_tokens"] += pt
            stage_bucket["completion_tokens"] += ct
            stage_bucket["total_tokens"] += tt

            prompt_details = raw_usage.get("prompt_tokens_details") or {}
            if isinstance(prompt_details, dict) and isinstance(
                prompt_details.get("cached_tokens"), int
            ):
                usage_acc.cached_tokens += int(prompt_details["cached_tokens"])
            comp_details = raw_usage.get("completion_tokens_details") or {}
            if isinstance(comp_details, dict) and isinstance(
                comp_details.get("reasoning_tokens"), int
            ):
                usage_acc.reasoning_tokens += int(comp_details["reasoning_tokens"])
        elif not replayed:
            usage_acc.unknown_usage_calls += 1

        return parsed
