"""Shared per-model pacing for bulk workers; account limits are not per process."""

import asyncio
import base64
import fcntl
import hashlib
import json
import math
import os
import re
import struct
import time
from pathlib import Path

from .common import write_json


def estimate_chat_tokens(messages, max_tokens):
    tokens = max_tokens
    for message in messages:
        content = message.get("content", "")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
        for part in parts:
            if part.get("type") == "text":
                text = part.get("text", "")
                chinese = len(re.findall(r"[\u3400-\u9fff]", text))
                tokens += chinese + math.ceil((len(text) - chinese) / 3)
            elif part.get("type") == "image_url":
                url = part["image_url"]["url"]
                try:
                    header = base64.b64decode(url.split(",", 1)[1][:48])
                    width, height = struct.unpack(">II", header[16:24])
                    tokens += min(5000, math.ceil(width * height / (28 * 28) * 1.3))
                except (ValueError, IndexError, struct.error):
                    tokens += 4000
    return max(256, tokens)


class ModelPacer:
    def __init__(self, model: str, *, embedding=False):
        directory = os.environ.get("DOCUMENT_MODEL_RATE_DIR")
        self.root = Path(directory) if directory else None
        self.key = hashlib.sha256(model.encode()).hexdigest()[:20]
        variable = "DOCUMENT_EMBEDDING_TPM" if embedding else "DOCUMENT_VLM_TPM"
        self.limit = max(1000, int(os.environ.get(variable, "350000" if embedding else "30000")))
        self.reserved_tokens = 0
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    def _change(self, callback):
        if self.root is None:
            return None
        with (self.root / (self.key + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / (self.key + ".json")
            state = (
                json.loads(path.read_text())
                if path.exists()
                else {
                    "effective_tpm": self.limit,
                    "next_at": 0,
                    "cooldown_until": 0,
                    "requests": 0,
                    "successful_requests": 0,
                    "rate_limited_requests": 0,
                    "observed_tokens": 0,
                }
            )
            result = callback(state)
            state["updated_at"] = time.time()
            write_json(path, state)
            return result

    async def acquire(self, estimated_tokens: int):
        if self.root is None:
            return
        while True:

            def reserve(state):
                current = time.time()
                wait = max(state["next_at"], state["cooldown_until"]) - current
                if wait > 0:
                    return wait
                state["next_at"] = current + estimated_tokens * 60 / state["effective_tpm"]
                state["requests"] += 1
                return 0

            wait = self._change(reserve)
            if wait <= 0:
                self.reserved_tokens = estimated_tokens
                return
            await asyncio.sleep(min(wait, 15))

    async def limited(self, retry_after=65):
        retry_after = max(65, min(300, retry_after))

        def update(state):
            state["cooldown_until"] = max(state["cooldown_until"], time.time() + retry_after)
            state["effective_tpm"] = max(5000, int(state["effective_tpm"] * 0.7))
            state["rate_limited_requests"] += 1
            state["last_limit_at"] = time.time()

        self._change(update)
        if self.root is None:
            await asyncio.sleep(retry_after)

    def succeeded(self, tokens=0):
        def update(state):
            state["successful_requests"] += 1
            state["observed_tokens"] += tokens
            if tokens > 0 and self.reserved_tokens > 0:
                correction = (tokens - self.reserved_tokens) * 60 / state["effective_tpm"]
                state["next_at"] = max(time.time(), state["next_at"] + correction)
            if (
                state["successful_requests"] % 50 == 0
                and time.time() - state.get("last_limit_at", 0) > 600
            ):
                state["effective_tpm"] = min(self.limit, int(state["effective_tpm"] * 1.2))

        self._change(update)
