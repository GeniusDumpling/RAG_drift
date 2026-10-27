"""Optional query/document reranking, retaining every candidate or falling back atomically."""

import asyncio
from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.content import ContentChunk
from app.schemas.search import EvidenceObject


@dataclass(frozen=True)
class RerankResult:
    evidence: list[EvidenceObject]
    trace: dict[str, Any]


class SiliconFlowReranker:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    async def rerank(
        self, query: str, evidence: list[EvidenceObject], texts: dict[str, str]
    ) -> RerankResult:
        started = perf_counter()
        trace: dict[str, Any] = {
            "provider": self.settings.reranker_provider,
            "model": self.settings.reranker_model,
            "candidate_count": len(evidence),
            "status": "disabled",
            "latency_ms": 0,
        }

        def result() -> RerankResult:
            trace["latency_ms"] = round((perf_counter() - started) * 1000)
            return RerankResult(evidence, trace)

        if self.settings.reranker_provider == "none":
            return result()
        if not evidence:
            trace["status"] = "empty"
            return result()
        trace["status"] = "fallback"
        key = (
            self.settings.reranker_api_key
            or self.settings.embedding_api_key
            or self.settings.vlm_api_key
        )
        if not key or not key.strip():
            trace["reason"] = "missing_api_key"
            return result()
        documents = []
        truncated_ids = []
        for item in evidence:
            body = texts.get(str(item.chunk_id), "").strip()
            if not body:
                trace["reason"] = "missing_chunk_text"
                return result()
            document = f"标题: {item.title or '无标题'}\n正文: {body}"
            if len(document) > self.settings.reranker_document_max_chars:
                truncated_ids.append(str(item.chunk_id))
            documents.append(document[: self.settings.reranker_document_max_chars])
        trace["input_chars"] = sum(map(len, documents))
        trace["truncated_chunk_ids"] = truncated_ids

        async def request(client: httpx.AsyncClient) -> Any:
            response = await client.post(
                self.settings.reranker_base_url.rstrip("/") + "/rerank",
                headers={"Authorization": f"Bearer {key.strip()}"},
                json={
                    "model": self.settings.reranker_model,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                    "return_documents": False,
                },
            )
            response.raise_for_status()
            return response.json()

        try:
            # An overall deadline also bounds services sending an endless trickle of bytes.
            async with asyncio.timeout(self.settings.reranker_timeout_seconds):
                if self.client is not None:
                    payload = await request(self.client)
                else:
                    async with httpx.AsyncClient(
                        timeout=self.settings.reranker_timeout_seconds
                    ) as client:
                        payload = await request(client)
            items = payload["results"]
            if not isinstance(items, list) or len(items) != len(evidence):
                raise ValueError("Reranker must return every candidate")
            scores = {}
            for ranked_item in items:
                index, score = ranked_item["index"], ranked_item["relevance_score"]
                if (
                    type(index) is not int
                    or not 0 <= index < len(evidence)
                    or index in scores
                    or type(score) not in (int, float)
                    or not isfinite(score)
                ):
                    raise ValueError("Invalid reranker index or score")
                scores[index] = float(score)
            order = sorted(scores, key=lambda index: (-scores[index], index))
            reranked = [
                evidence[index].model_copy(update={"rerank_score": scores[index]})
                for index in order
            ]
            trace.update(
                status="reranked",
                latency_ms=round((perf_counter() - started) * 1000),
                rankings=[
                    {
                        "chunk_id": str(evidence[index].chunk_id),
                        "original_rank": index + 1,
                        "rank": rank,
                        "score": scores[index],
                    }
                    for rank, index in enumerate(order, 1)
                ],
            )
            return RerankResult(reranked, trace)
        except httpx.HTTPStatusError as exc:
            trace["reason"] = f"http_{exc.response.status_code}"
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            # Do not log exception messages, response bodies, keys, or document contents.
            trace["reason"] = type(exc).__name__
        return result()


async def rerank_evidence(
    session: AsyncSession, query: str, evidence: list[EvidenceObject], settings: Settings
) -> RerankResult:
    texts = {}
    if settings.reranker_provider != "none" and evidence:
        chunks = (
            await session.scalars(
                select(ContentChunk).where(
                    ContentChunk.id.in_([item.chunk_id for item in evidence]),
                    ContentChunk.embed_status != "obsolete",
                )
            )
        ).all()
        expected = {item.chunk_id: item.content_item_id for item in evidence}
        texts = {
            str(chunk.id): chunk.display_text
            for chunk in chunks
            if expected[chunk.id] == chunk.content_item_id
        }
    return await SiliconFlowReranker(settings).rerank(query, evidence, texts)
