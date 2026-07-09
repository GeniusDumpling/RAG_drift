from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.literature.ieee import IEEEProvider
from app.literature.llm import LiteratureLLM, verify_analysis
from app.literature.ranking import pick_top
from app.literature.report import render_report
from app.models.literature import LiteratureRun
from app.repositories.literature import LiteratureRepository


class RunCancelledError(RuntimeError):
    pass


class LiteraturePipeline:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.repo = LiteratureRepository(session)
        self.llm = LiteratureLLM(self.settings)

    async def execute(self, run_id: uuid.UUID) -> LiteratureRun:
        run = await self.repo.get_run(run_id)
        if run is None:
            raise RuntimeError(f"Literature run {run_id} does not exist")
        try:
            return await self._execute(run)
        except RunCancelledError:
            await self.session.rollback()
            cancelled = await self.repo.get_run(run_id)
            if cancelled is None:
                raise
            return cancelled
        except Exception as exc:
            await self.session.rollback()
            failed = await self.repo.get_run(run_id)
            if failed is None:
                raise
            if failed.status == "cancelled":
                return failed
            failed.status = "failed"
            failed.current_stage = "failed"
            failed.progress_message = "文献研究任务失败。"
            failed.error_message = _safe_error(exc)
            failed.finished_at = datetime.now(UTC)
            await self.repo.add_event(
                failed.id,
                stage="failed",
                level="error",
                event_type="run_failed",
                message=failed.error_message,
            )
            await self.session.commit()
            return failed

    async def _execute(self, run: LiteratureRun) -> LiteratureRun:
        options = run.options_json
        direction_count = int(options.get("direction_count", 5))
        candidates_per_direction = int(options.get("candidates_per_direction", 25))
        top_n = int(options.get("top_n_per_direction", 1))
        year_from = int(options.get("year_from", 2018))
        include_fulltext = bool(options.get("include_fulltext", True))

        await self._check_cancelled(run)
        await self.repo.set_progress(
            run,
            stage="planning",
            current=1,
            message=f"正在生成 {direction_count} 个 IEEE 检索方向。",
            event_type="planning_started",
        )
        await self.session.commit()
        directions = await self.llm.generate_directions(run.query, direction_count=direction_count)
        run.directions_json = _json_value(directions)
        run.progress_total = max(1, 3 + len(directions) + len(directions) * top_n * 2)
        await self.repo.add_artifact(
            run_id=run.id,
            artifact_type="directions_json",
            json_data=run.directions_json,
            mime_type="application/json",
        )
        await self.repo.add_event(
            run.id,
            stage="planning",
            event_type="directions_generated",
            message=f"已生成 {len(directions)} 个检索方向。",
            counters={"directions": len(directions)},
        )
        await self.session.commit()

        search_results: dict[str, list[dict[str, Any]]] = {}
        selected: list[dict[str, Any]] = []

        async def provider_progress(stage: str, message: str, counters: dict[str, Any]) -> None:
            await self.session.refresh(run, ["status"])
            if run.status == "cancelled":
                raise RunCancelledError()
            await self.repo.set_progress(
                run,
                stage=stage,
                current=run.progress_current,
                message=message,
                status="waiting_for_login" if stage == "waiting_for_login" else "running",
                counters=counters,
            )
            await self.session.commit()

        async with IEEEProvider(self.settings, provider_progress) as provider:
            for index, direction in enumerate(directions, 1):
                await self._check_cancelled(run)
                message = f"正在检索方向 {index}/{len(directions)}：{direction['title_zh']}"
                await self.repo.set_progress(
                    run,
                    stage="searching",
                    current=1 + index - 1,
                    message=message,
                    counters={"current": index, "total": len(directions)},
                )
                await self.session.commit()
                try:
                    records = await provider.search(
                        str(direction["query"]), candidates_per_direction
                    )
                except Exception as exc:
                    records = []
                    await self.repo.add_event(
                        run.id,
                        stage="searching",
                        level="error",
                        event_type="direction_search_failed",
                        message=f"方向 {direction['id']} 检索失败：{_safe_error(exc)}",
                    )
                search_results[str(direction["id"])] = _json_value(records)
                run.raw_search_results_json = dict(search_results)
                await self.repo.set_progress(
                    run,
                    stage="searching",
                    current=1 + index,
                    message=f"方向 {direction['id']} 检索完成，命中 {len(records)} 条。",
                    counters={
                        "current": index,
                        "total": len(directions),
                        "records": len(records),
                    },
                    event_type="direction_search_completed",
                )
                await self.session.commit()

            await self.repo.add_artifact(
                run_id=run.id,
                artifact_type="search_results_json",
                json_data=run.raw_search_results_json,
                mime_type="application/json",
            )
            ranking_current = 2 + len(directions)
            await self.repo.set_progress(
                run,
                stage="ranking",
                current=ranking_current,
                message="正在对候选论文排序并保存入选关系。",
                event_type="ranking_started",
            )
            await self.session.commit()

            picks_json: list[dict[str, Any]] = []
            for direction in directions:
                records = search_results.get(str(direction["id"]), [])
                terms = direction.get("matched_terms")
                keywords = [str(term) for term in terms] if isinstance(terms, list) else []
                picks = pick_top(
                    records,
                    keywords=keywords,
                    year_from=year_from,
                    top_n=top_n,
                )
                for picked in picks:
                    record = _json_value(picked["record"])
                    paper = await self.repo.upsert_paper(record)
                    link = await self.repo.create_run_paper(
                        run_id=run.id,
                        paper_id=paper.id,
                        direction=direction,
                        candidate_rank=picked["candidate_rank"],
                        selected_rank=picked["selected_rank"],
                        score=picked["score"],
                        score_detail=picked["score_detail"],
                    )
                    await self.repo.add_artifact(
                        run_id=run.id,
                        paper_id=paper.id,
                        artifact_type="paper_metadata_json",
                        json_data=record,
                        mime_type="application/json",
                        source_url=paper.document_url,
                    )
                    item = {
                        "direction": direction,
                        "direction_id": direction["id"],
                        "record": record,
                        "paper": paper,
                        "link": link,
                        "document": {"pdf": None, "pages": [], "text": ""},
                    }
                    selected.append(item)
                    picks_json.append(
                        {
                            "direction_id": direction["id"],
                            "paper_id": str(paper.id),
                            "candidate_rank": picked["candidate_rank"],
                            "selected_rank": picked["selected_rank"],
                            "score": picked["score"],
                            "score_detail": picked["score_detail"],
                            "paper": record,
                        }
                    )
            run.picks_json = picks_json
            download_steps = len(selected) if include_fulltext else 0
            run.progress_total = 3 + len(directions) + download_steps + len(selected)
            await self.repo.add_artifact(
                run_id=run.id,
                artifact_type="picks_json",
                json_data=picks_json,
                mime_type="application/json",
            )
            await self.repo.add_event(
                run.id,
                stage="ranking",
                event_type="ranking_completed",
                message=f"排序完成，共入选 {len(selected)} 篇论文。",
                counters={"selected": len(selected)},
            )
            await self.session.commit()

            current = ranking_current
            if include_fulltext:
                for index, item in enumerate(selected, 1):
                    await self._check_cancelled(run)
                    current += 1
                    record = item["record"]
                    title = str(record.get("articleTitle") or "Untitled")
                    await self.repo.set_progress(
                        run,
                        stage="downloading",
                        current=current - 1,
                        message=f"正在获取 PDF/正文 {index}/{len(selected)}：{title[:80]}",
                    )
                    await self.session.commit()
                    try:
                        document = await provider.fetch_document(record)
                        item["document"] = document
                        pdf = document.get("pdf")
                        if isinstance(pdf, bytes):
                            await self.repo.add_artifact(
                                run_id=run.id,
                                paper_id=item["paper"].id,
                                artifact_type="paper_pdf",
                                binary_data=pdf,
                                mime_type="application/pdf",
                                source_url=document.get("pdf_url"),
                            )
                        text = str(document.get("text") or "")
                        raw_pages = document.get("pages")
                        pages: list[dict[str, Any]] = (
                            [page for page in raw_pages if isinstance(page, dict)]
                            if isinstance(raw_pages, list)
                            else []
                        )
                        if text:
                            await self.repo.add_artifact(
                                run_id=run.id,
                                paper_id=item["paper"].id,
                                artifact_type="paper_fulltext",
                                json_data={"pages": pages},
                                text_data=text,
                                mime_type="text/plain",
                                source_url=document.get("source_url"),
                            )
                        await self.repo.add_event(
                            run.id,
                            stage="extracting",
                            event_type="paper_fulltext_completed",
                            message=(
                                f"论文正文处理完成：PDF={'是' if pdf else '否'}，"
                                f"字符数={len(text)}。"
                            ),
                            counters={"index": index, "total": len(selected), "chars": len(text)},
                        )
                    except Exception as exc:
                        await self.repo.add_event(
                            run.id,
                            stage="extracting",
                            level="error",
                            event_type="paper_fulltext_failed",
                            message=f"论文正文处理失败：{_safe_error(exc)}",
                        )
                    run.progress_current = current
                    await self.session.commit()

        analyses: list[dict[str, Any]] = []
        report_items: list[dict[str, Any]] = []
        failed_analyses = 0
        for index, item in enumerate(selected, 1):
            await self._check_cancelled(run)
            current += 1
            record = item["record"]
            link = item["link"]
            document = item["document"]
            title = str(record.get("articleTitle") or "Untitled")
            await self.repo.set_progress(
                run,
                stage="analyzing",
                current=current - 1,
                message=f"正在分析论文 {index}/{len(selected)}：{title[:80]}",
            )
            await self.session.commit()
            try:
                excerpt = _excerpt(
                    str(document.get("text") or ""),
                    self.settings.literature_fulltext_max_chars,
                )
                raw_analysis = await self.llm.analyze_paper(
                    user_query=run.query,
                    direction=item["direction"],
                    paper=record,
                    fulltext_excerpt=excerpt,
                )
                raw_pages = document.get("pages")
                analysis_pages: list[dict[str, Any]] = (
                    [page for page in raw_pages if isinstance(page, dict)]
                    if isinstance(raw_pages, list)
                    else []
                )
                analysis, evidence_rows = verify_analysis(
                    raw_analysis,
                    title=title,
                    abstract=str(record.get("abstract") or ""),
                    pages=analysis_pages,
                )
                link.abstract_zh = str(analysis.get("abstract_zh") or "") or None
                link.match_how = str(analysis.get("match_how") or "") or None
                link.match_use = str(analysis.get("match_use") or "") or None
                link.conclusion = str(analysis.get("conclusion") or "") or None
                link.analysis_json = _json_value(analysis)
                link.analysis_status = "success"
                await self.repo.replace_evidence(link.id, evidence_rows)
                await self.repo.add_artifact(
                    run_id=run.id,
                    paper_id=item["paper"].id,
                    artifact_type="analysis_json",
                    json_data=link.analysis_json,
                    mime_type="application/json",
                )
            except Exception as exc:
                failed_analyses += 1
                analysis = {
                    "match_how": f"自动分析失败：{_safe_error(exc)}",
                    "match_use": "",
                    "conclusion": "",
                    "hits": [],
                }
                link.analysis_json = analysis
                link.analysis_status = "failed"
                await self.repo.add_event(
                    run.id,
                    stage="analyzing",
                    level="error",
                    event_type="paper_analysis_failed",
                    message=f"论文分析失败：{_safe_error(exc)}",
                )
            analyses.append(
                {
                    "paper_id": str(item["paper"].id),
                    "direction_id": item["direction_id"],
                    **_json_value(analysis),
                }
            )
            report_items.append(
                {
                    "paper": record,
                    "direction_id": item["direction_id"],
                    "analysis": analysis,
                }
            )
            run.analyses_json = list(analyses)
            run.progress_current = current
            await self.repo.add_event(
                run.id,
                stage="analyzing",
                event_type="paper_analysis_completed",
                message=f"论文分析完成 {index}/{len(selected)}。",
                counters={"index": index, "total": len(selected)},
            )
            await self.session.commit()

        await self._check_cancelled(run)
        report = render_report(query=run.query, directions=directions, results=report_items)
        run.final_report_markdown = report
        await self.repo.add_artifact(
            run_id=run.id,
            artifact_type="final_report",
            text_data=report,
            mime_type="text/markdown",
        )
        expected = len(directions) * top_n
        partial = len(selected) < expected or failed_analyses > 0
        run.status = "partial" if partial else "success"
        run.current_stage = run.status
        run.progress_current = run.progress_total
        run.progress_message = f"任务完成：入选 {len(selected)} 篇，分析失败 {failed_analyses} 篇。"
        run.finished_at = datetime.now(UTC)
        await self.repo.add_event(
            run.id,
            stage=run.status,
            level="warning" if partial else "info",
            event_type="run_completed",
            message=run.progress_message,
            counters={"selected": len(selected), "analysis_failed": failed_analyses},
        )
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def _check_cancelled(self, run: LiteratureRun) -> None:
        await self.session.refresh(run, ["status"])
        if run.status == "cancelled":
            raise RunCancelledError()


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    return message[:2000]


def _excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    paragraphs = [part.strip() for part in text.split("\n\n") if len(part.strip()) >= 40]
    priority = []
    for index, paragraph in enumerate(paragraphs):
        lowered = paragraph[:160].casefold()
        score = 0
        if any(word in lowered for word in ("method", "experiment", "result", "conclusion")):
            score += 3
        if any(char.isdigit() for char in paragraph):
            score += 1
        priority.append((score, index, paragraph))
    selected: list[tuple[int, str]] = []
    size = 0
    for _score, index, paragraph in sorted(priority, key=lambda row: (-row[0], row[1])):
        if size + len(paragraph) > max_chars and selected:
            continue
        selected.append((index, paragraph[: max_chars - size]))
        size += len(selected[-1][1])
        if size >= max_chars:
            break
    selected.sort(key=lambda row: row[0])
    return "\n\n".join(paragraph for _index, paragraph in selected)[:max_chars]
