"""Exact multimodal request caching, bounded real calls and provider usage accounting."""

import asyncio
import base64
import json
from pathlib import Path

import httpx

from app.services.video_graph.identity import stable_hash
from app.services.video_graph.usage import TokenUsage

from .source import checked_path, read_json, save_json, sha256


def parse_json_object(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Only close already complete records/arrays on a provider-declared stop.
        # Never complete strings, field values or a stream truncated by token limits.
        stack = []
        quoted = False
        escaped = False
        for char in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                if not stack or stack.pop() != {"]": "[", "}": "{"}[char]:
                    raise ValueError("JSON结构不匹配") from None
        if quoted or not stack or raw[-1:] not in "]}":
            raise ValueError("JSON内容未完整结束") from None
        parsed = json.loads(raw + "".join({"[": "]", "{": "}"}[v] for v in reversed(stack)))
    if not isinstance(parsed, dict):
        raise ValueError("模型须返回JSON对象")
    return parsed


class VisionClient:
    def __init__(self, settings, cache, *, cache_only=False, heartbeat=None):
        self.settings, self.cache = settings, Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.cache_only, self.heartbeat = cache_only, heartbeat
        self.model = getattr(settings, "document_graph_vlm_model", None) or settings.vlm_model
        self.usage = TokenUsage()
        self.trace = []
        self.endpoint_hash = stable_hash([settings.vlm_base_url.rstrip("/"), settings.vlm_api_key])

    async def request_text(self, system, payload, *, root, stage):
        """Native text extraction uses the configured text model; vision reviews layout."""
        from types import SimpleNamespace

        text_settings = SimpleNamespace(
            vlm_base_url=self.settings.base_url,
            vlm_api_key=self.settings.llm_api_key,
            vlm_model=self.settings.llm_model,
        )
        client = VisionClient(
            text_settings, self.cache, cache_only=self.cache_only, heartbeat=self.heartbeat
        )
        client.usage, client.trace = self.usage, self.trace
        return await client.request(system, payload, [], root=root, stage=stage)

    async def request(self, system, payload, images, *, root, stage):
        from tools.drone_documents.rate_limit import ModelPacer, estimate_chat_tokens

        content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
        for label, asset in images:
            raw = checked_path(root, asset["storage_key"]).read_bytes()
            if sha256(raw) != asset["sha256"]:
                raise ValueError("模型请求图片哈希不符")
            content.extend(
                [
                    {"type": "text", "text": label},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{asset['mime_type']};base64,"
                            + base64.b64encode(raw).decode(),
                        },
                    },
                ]
            )
        body = dict(
            model=self.model,
            temperature=0,
            max_tokens=512
            if stage == "label_observation"
            else (2048 if stage.endswith("observation") else 6144),
            stream=True,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content if images else content[0]["text"]},
            ],
        )
        if images:
            body.pop("response_format", None)
        if stage.endswith("observation"):
            body["frequency_penalty"] = 0.4
        if self.model.lower().startswith("deepseek"):
            body["thinking"] = {"type": "disabled"}
            body["reasoning_effort"] = "none"
        key = stable_hash([self.endpoint_hash, body])
        path = self.cache / (key + ".json")
        if path.exists():
            cached = read_json(path)
            if (
                cached.get("request_hash") != key
                or cached.get("response_hash") != stable_hash(cached.get("response"))
                or not isinstance(cached.get("response"), dict)
            ):
                raise ValueError("多模态响应缓存损坏")
            self.trace.append(
                dict(
                    model=self.model,
                    stage=stage,
                    request_hash=key,
                    replayed=True,
                    historical_usage=cached.get("usage"),
                )
            )
            self.usage.replay(stage)
            return cached["response"]
        if self.cache_only:
            raise ValueError("离线缓存缺少精确图文请求，不调用模型")
        if not self.settings.vlm_api_key:
            raise ValueError("多模态图谱需要 VLM_API_KEY")
        pacer = ModelPacer(self.model)
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:
            for attempt in range(2):
                if self.heartbeat:
                    self.heartbeat()
                await pacer.acquire(estimate_chat_tokens(body["messages"], body["max_tokens"]))
                if self.heartbeat:
                    self.heartbeat()
                self.usage.attempt(stage)
                envelope = None
                try:
                    envelope = await self._stream(client, body, stage)
                    usage = envelope.get("usage")
                    pacer.succeeded((usage or {}).get("total_tokens", 0))
                    choice = envelope["choices"][0]
                    if choice.get("finish_reason") == "length":
                        raise ValueError("视觉模型输出截断，不能入图")
                    raw = choice["message"]["content"]
                    parsed = parse_json_object(raw)
                    save_json(
                        path,
                        dict(
                            request_hash=key,
                            response=parsed,
                            response_hash=stable_hash(parsed),
                            usage=usage,
                        ),
                    )
                    self.trace.append(
                        dict(
                            model=self.model,
                            stage=stage,
                            request_hash=key,
                            replayed=False,
                            usage=usage,
                        )
                    )
                    return parsed
                except httpx.HTTPStatusError as exc:
                    self.trace.append(
                        dict(
                            model=self.model,
                            stage=stage,
                            request_hash=key,
                            replayed=False,
                            attempt=attempt + 1,
                            error_type=f"HTTP{exc.response.status_code}",
                        )
                    )
                    if exc.response.status_code in {401, 402, 403}:
                        raise RuntimeError(
                            f"视觉模型服务不可用（HTTP {exc.response.status_code}）"
                        ) from None
                    if attempt or exc.response.status_code not in {429, 500, 502, 503, 504}:
                        raise RuntimeError(
                            f"视觉模型调用失败（HTTP {exc.response.status_code}）"
                        ) from None
                    if exc.response.status_code == 429:
                        await pacer.limited(65)
                    await asyncio.sleep(2)
                except (httpx.TransportError, TimeoutError, ValueError, KeyError, TypeError) as exc:
                    self.trace.append(
                        dict(
                            model=self.model,
                            stage=stage,
                            request_hash=key,
                            replayed=False,
                            attempt=attempt + 1,
                            error_type=type(exc).__name__,
                        )
                    )
                    if envelope is not None:
                        save_json(
                            self.cache / "errors" / f"{key}-{attempt}.json",
                            dict(
                                request_hash=key, error_type=type(exc).__name__, response=envelope
                            ),
                        )
                    if attempt:
                        raise RuntimeError(
                            f"视觉模型调用或解析失败（{type(exc).__name__}）"
                        ) from None
                    await asyncio.sleep(2)
        raise AssertionError("unreachable request state")

    async def _stream(self, client, body, stage):
        import time

        chunks, usage, finish, completed = [], None, None, False
        last_heartbeat = time.monotonic()
        async with asyncio.timeout(240):
            async with client.stream(
                "POST",
                self.settings.vlm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + self.settings.vlm_api_key},
                json=body,
            ) as response:
                response.raise_for_status()
                try:
                    async for line in response.aiter_lines():
                        if self.heartbeat and time.monotonic() - last_heartbeat >= 30:
                            self.heartbeat()
                            last_heartbeat = time.monotonic()
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            completed = True
                            break
                        event = json.loads(raw)
                        if event.get("error"):
                            raise ValueError("视觉流返回错误")
                        if event.get("usage") is not None:
                            usage = event["usage"]
                        choices = event.get("choices") or []
                        if choices:
                            value = (choices[0].get("delta") or {}).get("content")
                            if isinstance(value, str):
                                chunks.append(value)
                            if choices[0].get("finish_reason"):
                                finish = choices[0]["finish_reason"]
                finally:
                    self.usage.record(stage, usage)
        if not completed or finish not in {"stop", "length"} or not chunks:
            raise ValueError("视觉模型流未完整结束")
        return {
            "choices": [{"message": {"content": "".join(chunks)}, "finish_reason": finish}],
            "usage": usage,
        }
