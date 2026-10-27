import asyncio
import json

import httpx

from .rate_limit import ModelPacer, estimate_chat_tokens


async def chat_json(client, settings, messages, *, max_tokens=1800):
    """Real provider request; reject truncation and invalid JSON."""
    pacer = ModelPacer(settings.vlm_model)
    tokens = estimate_chat_tokens(messages, max_tokens)
    for attempt in range(8):
        try:
            await pacer.acquire(tokens)
            response = await client.post(
                settings.vlm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + settings.vlm_api_key},
                json={
                    "model": settings.vlm_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                    "frequency_penalty": 0.4,
                },
            )
            response.raise_for_status()
            payload = response.json()
            choice = payload["choices"][0]
            pacer.succeeded(payload.get("usage", {}).get("total_tokens", 0))
            if choice.get("finish_reason") == "length":
                raise ValueError("模型响应被截断")
            content = choice["message"]["content"]
            result = json.loads(content[content.find("{") : content.rfind("}") + 1])
            if not isinstance(result, dict):
                raise ValueError("模型未返回 JSON 对象")
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
                401,
                402,
                403,
            }:
                raise
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                if attempt == 7:
                    raise
                try:
                    delay = float(exc.response.headers.get("retry-after", "65"))
                except ValueError:
                    delay = 65
                await pacer.limited(delay)
                continue
            if attempt >= 2:
                raise
            await asyncio.sleep(2 * (attempt + 1))
