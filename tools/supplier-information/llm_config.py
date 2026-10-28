"""Use the backend's shared LLM settings for standalone supplier commands."""

import logging
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.core.llm_config import LLMSettings  # noqa: E402

logger = logging.getLogger(__name__)


def get_llm_config(task_config: dict) -> dict:
    settings = LLMSettings()
    if not settings.llm_api_key:
        raise SystemExit("未配置 LLM_API_KEY，请在项目根目录 .env 或环境变量中填写。")
    return {
        **task_config,
        "api_base": settings.base_url,
        "api_key": settings.llm_api_key,
        "model": settings.llm_model,
    }


def chat_completion(api_key, conf, system_prompt, user_content):
    """Reject empty/truncated output; bounded retries can grow the completion budget."""
    budget = int(conf.get("max_tokens", 16384))
    last_error = None
    for attempt in range(int(conf.get("max_retries", 3))):
        try:
            response = requests.post(
                conf["api_base"].rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": conf["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": conf.get("temperature", 0.2),
                    "max_tokens": budget,
                    "stream": False,
                },
                timeout=conf.get("timeout", 120),
            )
            response.raise_for_status()
            result = response.json()
            choice = result["choices"][0]
            content = choice["message"].get("content") or ""
            finish = choice.get("finish_reason")
            logger.info(
                "LLM response: finish=%s output_chars=%d usage=%s",
                finish,
                len(content),
                result.get("usage", {}),
            )
            if finish == "length" or not content.strip():
                last_error = ValueError(f"Empty/truncated LLM completion (finish={finish})")
                budget = min(budget * 2, 32768)
                logger.warning("%s; next output budget=%d", last_error, budget)
                continue
            return content
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            last_error = exc
            if attempt + 1 < int(conf.get("max_retries", 3)):
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"Supplier LLM completion failed: {last_error}")
