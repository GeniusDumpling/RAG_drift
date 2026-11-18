"""Exact text requests with durable checkpoints, bounded retry and provider usage."""

import asyncio
import json
from pathlib import Path

import httpx

from app.services.knowledge_graph.schema import digest
from app.services.video_graph.usage import TokenUsage

from .source import save_json


class ForumClient:
    def __init__(self, settings, cache, *, cache_only=False, checkpoint=None, output=None):
        self.settings, self.cache = settings, Path(cache)
        self.cache_only, self.checkpoint = cache_only, checkpoint
        self.output = output if output is not None else {}
        self.usage = TokenUsage()
        self.trace = []

    def heartbeat(self):
        if self.checkpoint:
            self.checkpoint(self.output)

    async def request(self, stage, system, payload):
        model = (
            self.settings.forum_graph_assessment_model if stage.startswith("assessment") else None
        ) or self.settings.llm_model
        body = dict(
            model=model,
            temperature=0,
            max_tokens=8192,
            response_format={"type": "json_object"},
            messages=[
                dict(role="system", content=system),
                dict(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
        )
        if model.lower().startswith("deepseek"):
            body.update(thinking={"type": "disabled"}, reasoning_effort="none")
        key = digest([self.settings.base_url.rstrip("/"), self.settings.llm_api_key, body])
        checkpoints = self.output.setdefault("requests", {})
        cached = checkpoints.get(key)
        path = self.cache / f"{key}.json"
        if cached is None and path.is_file():
            try:
                cached = json.loads(path.read_text())
            except (ValueError, OSError):
                if self.cache_only:
                    raise ValueError("论坛离线响应缓存无法解析") from None
        if cached is not None:
            valid = (
                isinstance(cached, dict)
                and cached.get("key") == key
                and isinstance(cached.get("response"), dict)
                and cached.get("hash") == digest(cached.get("response"))
            )
            if valid:
                checkpoints[key] = cached
                self.usage.replay(stage)
                self.trace.append(dict(stage=stage, request_hash=key, replayed=True))
                return cached["response"]
            if self.cache_only:
                raise ValueError("论坛离线模型响应缓存损坏")
            checkpoints.pop(key, None)
        if self.cache_only:
            raise ValueError("缺少精确请求缓存，禁止离线模式调用模型")
        if not self.settings.llm_api_key:
            raise ValueError("论坛图谱需要配置 LLM_API_KEY")
        async with httpx.AsyncClient(timeout=self.settings.forum_graph_timeout_seconds) as client:
            for attempt in range(2):
                self.heartbeat()
                self.usage.attempt(stage)
                task = asyncio.create_task(
                    client.post(
                        self.settings.base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + self.settings.llm_api_key},
                        json=body,
                    )
                )
                recorded_usage = False
                response = None
                try:
                    while not task.done():
                        await asyncio.wait({task}, timeout=25)
                        self.heartbeat()
                    response = await task
                    response.raise_for_status()
                    envelope = response.json()
                    self.usage.record(stage, envelope.get("usage"))
                    recorded_usage = True
                    choice = envelope["choices"][0]
                    if choice.get("finish_reason") != "stop":
                        raise ValueError("模型未完整结束，不接受截断结果")
                    parsed = json.loads(choice["message"]["content"])
                    if not isinstance(parsed, dict):
                        raise ValueError("模型须返回 JSON 对象")
                    cached = dict(key=key, response=parsed, hash=digest(parsed))
                    save_json(path, cached)
                    checkpoints[key] = cached
                    self.trace.append(dict(stage=stage, request_hash=key, replayed=False))
                    self.heartbeat()
                    return parsed
                except (httpx.TransportError, httpx.HTTPStatusError, ValueError, KeyError) as exc:
                    if response is not None and not recorded_usage:
                        self.usage.record(stage, None)
                    retryable = isinstance(exc, httpx.TransportError | ValueError | KeyError) or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code in {429, 500, 502, 503, 504}
                    )
                    self.trace.append(
                        dict(
                            stage=stage,
                            request_hash=key,
                            error_type=type(exc).__name__,
                            attempt=attempt + 1,
                        )
                    )
                    if not retryable or attempt:
                        raise RuntimeError(f"论坛模型请求失败（{type(exc).__name__}）") from None
                    delay = 2
                    if isinstance(exc, httpx.HTTPStatusError):
                        try:
                            delay = max(delay, int(exc.response.headers.get("Retry-After", "2")))
                        except ValueError:
                            pass
                    if delay > 60:
                        raise RuntimeError("模型限流要求延后重试") from None
                    self.heartbeat()
                    await asyncio.sleep(delay)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
        raise AssertionError("unreachable")
