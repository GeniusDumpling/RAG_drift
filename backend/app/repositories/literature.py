from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import (
    LiteratureArtifact,
    LiteratureEvidence,
    LiteraturePaper,
    LiteratureRun,
    LiteratureRunEvent,
    LiteratureRunPaper,
)

TERMINAL_STATUSES = {"success", "partial", "failed", "cancelled"}


class LiteratureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(self, query: str, options: dict[str, Any]) -> LiteratureRun:
        direction_count = int(options.get("direction_count", 5))
        run = LiteratureRun(
            query=query,
            status="queued",
            current_stage="queued",
            progress_current=0,
            progress_total=max(1, 4 + direction_count * 3),
            progress_message="文献研究任务已进入队列。",
            options_json=options,
        )
        self.session.add(run)
        await self.session.flush()
        await self.add_event(
            run.id,
            stage="queued",
            event_type="run_queued",
            message="Literature research run queued.",
        )
        return run

    async def get_run(self, run_id: uuid.UUID) -> LiteratureRun | None:
        return await self.session.get(LiteratureRun, run_id)

    async def list_runs(self, limit: int, offset: int) -> tuple[list[LiteratureRun], int]:
        total = await self.session.scalar(select(func.count(LiteratureRun.id)))
        rows = await self.session.scalars(
            select(LiteratureRun)
            .order_by(LiteratureRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows), int(total or 0)

    async def list_events(
        self, run_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[LiteratureRunEvent], int]:
        where = LiteratureRunEvent.literature_run_id == run_id
        total = await self.session.scalar(select(func.count(LiteratureRunEvent.id)).where(where))
        rows = await self.session.scalars(
            select(LiteratureRunEvent)
            .where(where)
            .order_by(LiteratureRunEvent.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows), int(total or 0)

    async def add_event(
        self,
        run_id: uuid.UUID,
        *,
        stage: str,
        event_type: str,
        message: str,
        level: str = "info",
        counters: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> LiteratureRunEvent:
        event = LiteratureRunEvent(
            literature_run_id=run_id,
            stage=stage,
            level=level,
            event_type=event_type,
            message=message,
            counters_json=counters or {},
            trace_json=trace or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def set_progress(
        self,
        run: LiteratureRun,
        *,
        stage: str,
        current: int,
        message: str,
        status: str = "running",
        event_type: str = "stage_progress",
        counters: dict[str, Any] | None = None,
    ) -> None:
        run.status = status
        run.current_stage = stage
        run.progress_current = min(max(0, current), run.progress_total)
        run.progress_message = message
        await self.add_event(
            run.id,
            stage=stage,
            event_type=event_type,
            message=message,
            counters=counters,
        )
        await self.session.flush()

    async def claim_next_run(self, run_id: uuid.UUID | None = None) -> LiteratureRun | None:
        statement = select(LiteratureRun).where(LiteratureRun.status == "queued")
        if run_id is not None:
            statement = statement.where(LiteratureRun.id == run_id)
        statement = statement.order_by(LiteratureRun.created_at.asc()).with_for_update(
            skip_locked=True
        )
        run = await self.session.scalar(statement.limit(1))
        if run is None:
            return None
        run.status = "running"
        run.current_stage = "planning"
        run.progress_message = "Worker 已领取任务，开始生成研究方向。"
        run.started_at = datetime.now(UTC)
        await self.add_event(
            run.id,
            stage="planning",
            event_type="run_claimed",
            message="Literature worker claimed the run.",
        )
        await self.session.flush()
        return run

    async def cancel_run(self, run: LiteratureRun) -> LiteratureRun:
        if run.status in TERMINAL_STATUSES:
            return run
        run.status = "cancelled"
        run.current_stage = "cancelled"
        run.progress_message = "任务已取消。"
        run.finished_at = datetime.now(UTC)
        await self.add_event(
            run.id,
            stage="cancelled",
            event_type="run_cancelled",
            message="Literature research run cancelled by user.",
            level="warning",
        )
        await self.session.flush()
        return run

    async def upsert_paper(self, metadata: dict[str, Any]) -> LiteraturePaper:
        article_number = _string_or_none(
            metadata.get("articleNumber") or metadata.get("arnumber") or metadata.get("documentId")
        )
        doi = _string_or_none(metadata.get("doi"))
        title = str(metadata.get("articleTitle") or metadata.get("title") or "Untitled").strip()
        title_hash = hashlib.sha256(_normalized_title(title).encode("utf-8")).hexdigest()
        conditions = []
        if article_number:
            conditions.append(LiteraturePaper.ieee_article_number == article_number)
        if doi:
            conditions.append(LiteraturePaper.doi == doi)
        if not conditions:
            conditions.append(LiteraturePaper.title_hash == title_hash)
        paper = await self.session.scalar(select(LiteraturePaper).where(or_(*conditions)).limit(1))

        values = {
            "ieee_article_number": article_number,
            "doi": doi,
            "title": title,
            "title_hash": title_hash,
            "authors_json": _authors(metadata),
            "abstract": _string_or_none(metadata.get("abstract")),
            "publication_title": _string_or_none(
                metadata.get("publicationTitle") or metadata.get("publisher")
            ),
            "publication_year": _int_or_none(
                metadata.get("publicationYear") or metadata.get("publicationDate")
            ),
            "citation_count": _int_or_none(metadata.get("citationCount")) or 0,
            "access_type": _string_or_none(metadata.get("accessType")),
            "document_url": _document_url(metadata, article_number),
            "pdf_url": _pdf_url(metadata, article_number),
            "raw_metadata_json": metadata,
            "metadata_hash": hashlib.sha256(
                repr(sorted(metadata.items())).encode("utf-8", errors="replace")
            ).hexdigest(),
        }
        if paper is None:
            paper = LiteraturePaper(**values)
            self.session.add(paper)
        else:
            for key, value in values.items():
                if value is not None or key in {"raw_metadata_json", "metadata_hash"}:
                    setattr(paper, key, value)
        await self.session.flush()
        return paper

    async def create_run_paper(
        self,
        *,
        run_id: uuid.UUID,
        paper_id: uuid.UUID,
        direction: dict[str, Any],
        candidate_rank: int | None,
        selected_rank: int | None,
        score: int | None,
        score_detail: dict[str, Any],
    ) -> LiteratureRunPaper:
        link = LiteratureRunPaper(
            literature_run_id=run_id,
            paper_id=paper_id,
            direction_id=str(direction.get("id") or "D?"),
            direction_title=_string_or_none(direction.get("title_zh") or direction.get("title_en")),
            search_query=str(direction.get("query") or ""),
            candidate_rank=candidate_rank,
            selected_rank=selected_rank,
            score=score,
            score_detail_json=score_detail,
            selected=True,
            analysis_status="pending",
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def add_artifact(
        self,
        *,
        run_id: uuid.UUID,
        artifact_type: str,
        paper_id: uuid.UUID | None = None,
        json_data: dict[str, Any] | list[Any] | None = None,
        text_data: str | None = None,
        binary_data: bytes | None = None,
        mime_type: str | None = None,
        source_url: str | None = None,
    ) -> LiteratureArtifact:
        data = binary_data if binary_data is not None else (text_data or "").encode("utf-8")
        artifact = LiteratureArtifact(
            literature_run_id=run_id,
            paper_id=paper_id,
            artifact_type=artifact_type,
            json_data=json_data if json_data is not None else {},
            text_data=text_data,
            binary_data=binary_data,
            mime_type=mime_type,
            byte_size=len(data) if data else None,
            sha256=hashlib.sha256(data).hexdigest() if data else None,
            source_url=source_url,
        )
        self.session.add(artifact)
        await self.session.flush()
        return artifact

    async def replace_evidence(
        self, run_paper_id: uuid.UUID, evidence_rows: list[dict[str, Any]]
    ) -> list[LiteratureEvidence]:
        rows = []
        for values in evidence_rows:
            row = LiteratureEvidence(literature_run_paper_id=run_paper_id, **values)
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        return rows

    async def get_paper(self, paper_id: uuid.UUID) -> LiteraturePaper | None:
        return await self.session.get(LiteraturePaper, paper_id)

    async def get_artifact(self, artifact_id: uuid.UUID) -> LiteratureArtifact | None:
        return await self.session.get(LiteratureArtifact, artifact_id)

    async def result_rows(
        self, run_id: uuid.UUID
    ) -> list[tuple[LiteratureRunPaper, LiteraturePaper]]:
        result = await self.session.execute(
            select(LiteratureRunPaper, LiteraturePaper)
            .join(LiteraturePaper, LiteraturePaper.id == LiteratureRunPaper.paper_id)
            .where(LiteratureRunPaper.literature_run_id == run_id)
            .order_by(
                LiteratureRunPaper.direction_id.asc(),
                LiteratureRunPaper.selected_rank.asc().nullslast(),
            )
        )
        return list(result.tuples())

    async def evidence_for_links(
        self, link_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[LiteratureEvidence]]:
        if not link_ids:
            return {}
        rows = await self.session.scalars(
            select(LiteratureEvidence)
            .where(LiteratureEvidence.literature_run_paper_id.in_(link_ids))
            .order_by(LiteratureEvidence.created_at.asc())
        )
        grouped: dict[uuid.UUID, list[LiteratureEvidence]] = {}
        for row in rows:
            grouped.setdefault(row.literature_run_paper_id, []).append(row)
        return grouped

    async def artifacts_for_run(self, run_id: uuid.UUID) -> list[LiteratureArtifact]:
        rows = await self.session.scalars(
            select(LiteratureArtifact)
            .where(LiteratureArtifact.literature_run_id == run_id)
            .order_by(LiteratureArtifact.created_at.asc())
        )
        return list(rows)


def _normalized_title(value: str) -> str:
    return " ".join(value.casefold().split())


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    for token in text.replace("-", " ").split():
        if token.isdigit() and len(token) == 4:
            return int(token)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _authors(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    authors = metadata.get("authors") or metadata.get("author") or []
    if isinstance(authors, str):
        return [{"name": item.strip()} for item in authors.split(";") if item.strip()]
    if not isinstance(authors, list):
        return []
    return [item if isinstance(item, dict) else {"name": str(item)} for item in authors]


def _document_url(metadata: dict[str, Any], article_number: str | None) -> str | None:
    value = _string_or_none(metadata.get("documentLink") or metadata.get("documentUrl"))
    if value:
        return value if value.startswith("http") else f"https://ieeexplore.ieee.org{value}"
    return f"https://ieeexplore.ieee.org/document/{article_number}" if article_number else None


def _pdf_url(metadata: dict[str, Any], article_number: str | None) -> str | None:
    value = _string_or_none(metadata.get("pdfLink") or metadata.get("pdfUrl"))
    if value:
        return value if value.startswith("http") else f"https://ieeexplore.ieee.org{value}"
    if article_number:
        return f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={article_number}"
    return None
