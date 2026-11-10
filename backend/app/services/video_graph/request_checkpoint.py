"""Durable exact-request checkpoints scoped to one owned build and source chunk."""

import json

from app.services.video_graph.identity import stable_hash


class RequestCheckpoint:
    def __init__(self, output, extractor, persist):
        self.output, self.persist = output, persist
        self.identity = stable_hash(
            [
                extractor.version,
                extractor.settings.base_url.rstrip("/"),
                extractor.settings.llm_api_key,
            ]
        )
        self.namespace = "assessment"

    def clear(self):
        root = self.output.get("request_checkpoints")
        if not isinstance(root, dict):
            self.output.pop("request_checkpoints", None)
            return
        root.pop(self.namespace, None)
        if not self.output.get("request_checkpoints"):
            self.output.pop("request_checkpoints", None)

    def get(self, body, compute):
        key = stable_hash([self.identity, self.namespace, body])
        if not isinstance(self.output.get("request_checkpoints"), dict):
            self.output["request_checkpoints"] = {}
        root = self.output["request_checkpoints"]
        if not isinstance(root.get(self.namespace), dict):
            root[self.namespace] = {}
        rows = root[self.namespace]
        cached = rows.get(key)
        if isinstance(cached, dict):
            try:
                response = json.loads(cached.get("response_json", ""))
            except (TypeError, ValueError):
                response = None
            if isinstance(response, dict) and cached.get("response_hash") == stable_hash(response):
                return json.loads(json.dumps(response)), True
            rows.pop(key, None)
        response = compute()
        if not isinstance(response, dict):
            raise ValueError("阶段检查点只接受JSON对象")
        # JSONB reorders object keys. Preserve original nested key order so rebuilding
        # a downstream user-message JSON string produces exactly the same request.
        rows[key] = {
            "response_json": json.dumps(response, ensure_ascii=False),
            "response_hash": stable_hash(response),
        }
        try:
            self.persist()  # Commit under the existing lease before the next model call.
        except Exception:
            rows.pop(key, None)
            raise
        return json.loads(json.dumps(response)), False
