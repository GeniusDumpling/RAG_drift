"""Explicit evaluation-only caching of exact model requests; never a production graph cache."""

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.services.video_graph import LLMVideoExtractor
from app.services.video_graph_identity import stable_hash
from app.services.video_graph_usage import request_stage


class ResponseCacheMiss(ValueError):
    pass


class ModelResponseCache:
    def __init__(self, directory: Path, *, cache_only: bool = False):
        self.directory = directory
        self.cache_only = cache_only
        self._locks = {}
        self._guard = threading.Lock()
        directory.mkdir(parents=True, exist_ok=True)

    def get(self, *, namespace, endpoint_hash, request, compute):
        identity = {"namespace": namespace, "endpoint_hash": endpoint_hash, "request": request}
        key = stable_hash(identity)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            path = self.directory / (key + ".json")
            if path.exists():
                return self._load(path, identity), True, key
            if self.cache_only:
                raise ResponseCacheMiss("缓存中缺少当前精确请求，离线重放未调用模型")
            response = compute()
            if not isinstance(response, dict):
                raise ValueError("缓存只接受JSON对象模型响应")
            envelope = {**identity, "response": response, "response_hash": stable_hash(response),
                        "created_at": datetime.now(UTC).isoformat()}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                                 dir=self.directory, prefix=".response-") as handle:
                    temporary = Path(handle.name)
                    json.dump(envelope, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    # First writer pins the response even across evaluator processes.
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return self._load(path, identity), False, key

    @staticmethod
    def _load(path, identity):
        envelope = json.loads(path.read_text())
        if any(envelope.get(key) != value for key, value in identity.items()):
            raise ValueError("模型响应缓存的请求标识不一致")
        response = envelope.get("response")
        if not isinstance(response, dict) or envelope.get("response_hash") != stable_hash(response):
            raise ValueError("模型响应缓存内容校验失败")
        return response


class CachedEvaluationExtractor(LLMVideoExtractor):
    def __init__(self, settings, *, cache: ModelResponseCache, namespace: str):
        super().__init__(settings)
        self.cache, self.namespace = cache, namespace
        # Neither the endpoint nor credentials are stored as plaintext in cache files.
        self.endpoint_hash = stable_hash([settings.base_url.rstrip("/"), settings.llm_api_key])
        self.cache_stats = {"live_stages": 0, "replayed_stages": 0}
        self.cache_trace = []

    def _request(self, system, payload, *, phase="extraction"):
        stage = request_stage(phase, payload)
        before = self.token_usage.snapshot().get(stage, {})
        try:
            response, replayed, key = self.cache.get(
                namespace=self.namespace, endpoint_hash=self.endpoint_hash,
                request=self._request_body(system, payload, phase=phase),
                compute=lambda: super(CachedEvaluationExtractor, self)._request(
                    system, payload, phase=phase
                ),
            )
        except Exception:
            after = self.token_usage.snapshot().get(stage, {})
            if after != before:
                self.cache_trace.append({
                    "phase": phase, "stage": stage, "replayed": False, "failed": True,
                    "usage": {k: v - before.get(k, 0) for k, v in after.items()},
                })
            raise
        self.cache_stats["replayed_stages" if replayed else "live_stages"] += 1
        after = self.token_usage.snapshot().get(stage, {})
        self.cache_trace.append({
            "phase": phase, "stage": stage, "request_hash": key, "replayed": replayed,
            "usage": ({k: v - before.get(k, 0) for k, v in after.items()}
                      if not replayed else None),
        })
        return response
