#!/usr/bin/env python3
"""Inventory, backfill and incrementally maintain the video-only knowledge graph."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import app.models  # noqa: E402, F401
from app.core.config import Settings, get_settings  # noqa: E402
from app.models.content import ContentItem  # noqa: E402
from app.services.video_graph.backfill import apply_plan, create_plan, save_plan  # noqa: E402
from app.services.video_graph.extraction import PROMPT_VERSION, LLMVideoExtractor  # noqa: E402
from app.services.video_graph.identity import extraction_version  # noqa: E402
from app.services.video_graph.material import load_material  # noqa: E402
from app.services.video_graph.pipeline import run_pipeline  # noqa: E402
from app.services.video_graph.tasks import enqueue_videos, process_next  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def target_settings(settings: Settings, env_file: Path | None) -> Settings:
    """Override only the graph connection without changing the app's shared settings."""
    if env_file is None:
        return settings
    if not env_file.is_file():
        raise ValueError("Neo4j目标配置文件不存在，拒绝回退到默认图谱")
    from dotenv import dotenv_values

    values = dotenv_values(env_file, interpolate=False)
    fields = {
        "NEO4J_URI": "neo4j_uri",
        "NEO4J_USER": "neo4j_user",
        "NEO4J_PASSWORD": "neo4j_password",
        "NEO4J_DATABASE": "neo4j_database",
    }
    if set(values) - set(fields):
        raise ValueError("Neo4j目标配置只允许URI、用户、密码和数据库名")
    if any(not isinstance(values.get(key), str) or not values[key].strip() for key in fields):
        raise ValueError("Neo4j目标配置必须完整提供URI、用户、密码和数据库名")
    return settings.model_copy(update={field: values[key] for key, field in fields.items()})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "scan", "run", "watch", "plan-backfill", "backfill", "pipeline"):
        command = commands.add_parser(name)
        command.add_argument(
            "--neo4j-env-file", type=Path, help="仅为本次构图选择独立Neo4j连接配置"
        )
        command.add_argument(
            "--content-id", type=uuid.UUID, action="append", help="只处理指定视频，可重复"
        )
        command.add_argument(
            "--limit", type=int, default=20, help="展示或每轮处理的视频数量，默认 20"
        )
        command.add_argument(
            "--extractor-version", default=PROMPT_VERSION, help="改动抽取规则后递增此版本"
        )
        command.add_argument(
            "--retry-failed", action="store_true", help="重试失败及部分完成任务，复用成功切片"
        )
        command.add_argument(
            "--retry-partial", action="store_true", help="仅重新排队部分完成任务，补跑未通过切片"
        )
        command.add_argument(
            "--dry-run", action="store_true", help="只读盘点，不写数据库、不调用模型"
        )
        if name == "watch":
            command.add_argument("--interval", type=int, default=120, help="轮询间隔秒数，默认 120")
        if name in {"plan-backfill", "backfill", "pipeline"}:
            command.add_argument(
                "--manifest",
                type=Path,
                required=True,
                help="固定回补范围、材料指纹和目标版本的 JSON 清单",
            )
        if name == "pipeline":
            command.add_argument("--report", type=Path, required=True, help="完整管线的验证报告")
        if name in {"backfill", "pipeline"}:
            command.add_argument(
                "--batch-size",
                type=int,
                default=5,
                help="每次最多排队的视频数；--limit 限制本次总数",
            )
    for name in ("withdraw", "restore"):
        command = commands.add_parser(name)
        command.add_argument(
            "--neo4j-env-file", type=Path, help="选择要撤回或恢复的Neo4j目标"
        )
        command.add_argument("--content-id", type=uuid.UUID, required=True)
    args = parser.parse_args(argv)
    if getattr(args, "limit", 1) < 1:
        parser.error("--limit 必须大于 0")
    if getattr(args, "interval", 5) < 5:
        parser.error("--interval 必须至少为 5 秒")
    if getattr(args, "batch_size", 1) < 1:
        parser.error("--batch-size 必须大于 0")
    if args.command == "backfill" and args.content_id:
        parser.error("backfill 的范围来自清单；请在 plan-backfill 时指定 --content-id")
    return args


def inventory(session: Session, args: argparse.Namespace) -> dict[str, object]:
    query = select(ContentItem.id).where(ContentItem.item_type == "video_description")
    if args.content_id:
        query = query.where(ContentItem.id.in_(args.content_id))
    ids = session.scalars(query.order_by(ContentItem.created_at, ContentItem.id)).all()
    counts = {
        "videos": len(ids),
        "with_transcript": 0,
        "summary_only": 0,
        "no_supported_chunks": 0,
        "transcript_chunks": 0,
    }
    items = []
    for content_id in ids:
        material = load_material(session, content_id)
        assert material is not None
        transcripts = sum(c.evidence_type == "transcript_segment" for c in material.chunks)
        counts["transcript_chunks"] += transcripts
        counts[
            "with_transcript"
            if transcripts
            else "summary_only"
            if material.extraction_chunks
            else "no_supported_chunks"
        ] += 1
        if len(items) < args.limit:
            items.append(
                {
                    "id": str(content_id),
                    "title": material.title,
                    "source_key": material.source_key,
                    "transcript_chunks": transcripts,
                    "fingerprint": material.fingerprint,
                }
            )
    return {
        "mode": "read_only",
        "counts": counts,
        "items": items,
        "note": "盘点不判断主题相关性；构图时按视频判断主题，再逐切片抽取",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = target_settings(get_settings(), args.neo4j_env_file)
    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    store = None
    try:
        if args.command != "inventory" and (
            not getattr(args, "dry_run", False) or args.command == "pipeline"
        ):
            from app.services.video_graph.storage import GraphStore

            store = GraphStore(settings)
            if args.command != "plan-backfill" and not getattr(args, "dry_run", False):
                store.initialize()
            store.ensure_ready()
        if args.command in {"withdraw", "restore"}:
            with Session(engine) as session, session.begin():
                if load_material(session, args.content_id) is None:
                    raise ValueError("找不到视频记录")
                store.withdraw(args.content_id, restore=args.command == "restore")
            emit({"status": args.command, "content_item_id": str(args.content_id)})
            return 0
        if args.command == "inventory" or (args.dry_run and args.command != "pipeline"):
            with Session(engine) as session:
                emit(inventory(session, args))
            return 0
        version = extraction_version(
            args.extractor_version,
            settings.llm_model,
            settings.video_graph_reasoning_effort,
            settings.video_graph_extraction_effort,
        )
        if args.command == "pipeline":
            extractor = (
                SimpleNamespace(version=version)
                if args.dry_run
                else LLMVideoExtractor(settings, version=args.extractor_version)
            )
            final_status = None
            for event in run_pipeline(
                engine,
                extractor,
                args.manifest,
                args.report,
                store=store,
                content_ids=args.content_id,
                limit=args.limit,
                batch_size=args.batch_size,
                retry_failed=args.retry_failed or args.retry_partial,
                dry_run=args.dry_run,
            ):
                emit(event)
                if event.get("stage") == "pipeline_summary":
                    final_status = event.get("status")
            return 0 if args.dry_run or final_status == "success" else 1
        if args.command == "plan-backfill":
            plan = create_plan(engine, version, store=store, content_ids=args.content_id)
            save_plan(args.manifest, plan)
            emit(
                {
                    "stage": "backfill_plan",
                    "manifest": str(args.manifest),
                    "target_version": version,
                    "counts": plan["counts"],
                    "estimated_model_calls": sum(i["estimated_model_calls"] for i in plan["items"]),
                }
            )
            return 0
        extractor = (
            LLMVideoExtractor(settings, version=args.extractor_version)
            if args.command in {"run", "watch", "backfill"}
            else None
        )
        if args.command == "backfill":
            failed = False
            for event in apply_plan(
                engine,
                extractor,
                args.manifest,
                store=store,
                limit=args.limit,
                batch_size=args.batch_size,
                retry_failed=args.retry_failed or args.retry_partial,
            ):
                emit(event)
                failed = failed or event.get("status") in {
                    "failed",
                    "partial_success",
                    "lease_lost",
                    "obsolete",
                }
            return 1 if failed else 0
        failed = False
        while True:
            scan = enqueue_videos(
                engine,
                store,
                extractor_version=version,
                content_ids=args.content_id,
                retry_failed=args.retry_failed,
                retry_partial=args.retry_partial,
            )
            emit({"stage": "scan", **scan})
            if args.command == "scan":
                return 0
            assert extractor is not None
            for _ in range(args.limit):
                result = process_next(engine, store, extractor, content_ids=args.content_id)
                if result is None:
                    break
                emit(result)
                failed = failed or result["status"] == "failed"
            if args.command != "watch":
                return 1 if failed else 0
            # Failed jobs require an explicit retry; don't repeatedly charge for them in watch.
            args.retry_failed = False
            args.retry_partial = False
            time.sleep(args.interval)
    except KeyboardInterrupt:
        emit({"status": "interrupted", "note": "未完成任务会在租约过期后重试"})
        return 130
    except ValueError as exc:
        emit({"status": "error", "error": str(exc)})
        return 1
    except Exception as exc:
        emit(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "hint": "检查 Neo4j 连接和迁移状态、PostgreSQL 源内容连接；此工具不自动迁移",
            }
        )
        return 1
    finally:
        if store:
            store.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
