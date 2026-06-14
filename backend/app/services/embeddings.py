import hashlib
import math
from typing import Protocol


class EmbeddingService(Protocol):
    model_name: str
    dimension: int

    def embed(self, text: str) -> list[float]: ...


class DeterministicEmbeddingService:
    model_name: str = "deterministic-hash-v1"

    def __init__(self, dimension: int = 384) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        values: list[float] = []
        salt_index = 0
        encoded_text = text.encode("utf-8")
        while len(values) < self.dimension:
            digest = hashlib.sha256(salt_index.to_bytes(4, "big") + b"\0" + encoded_text).digest()
            values.extend((byte / 127.5) - 1.0 for byte in digest)
            salt_index += 1

        vector = values[: self.dimension]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return [0.0 for _ in vector]
        return [value / norm for value in vector]
