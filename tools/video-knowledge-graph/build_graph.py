#!/usr/bin/env python3
"""Inventory, backfill and incrementally maintain the video-only knowledge graph."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import app.models  # noqa: E402, F401
from app.core.config import get_settings  # noqa: E402
from app.models.content import ContentItem  # noqa: E402
from app.services.video_graph import (  # noqa: E402
    PROMPT_VERSION,
    DeepSeekVideoExtractor,
    enqueue_videos,
    load_material,
    process_next,
    withdraw_video,
)
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "scan", "run", "watch"):
        command = commands.add_parser(name)
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
    for name in ("withdraw", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--content-id", type=uuid.UUID, required=True)
    args = parser.parse_args(argv)
    if getattr(args, "limit", 1) < 1:
        parser.error("--limit 必须大于 0")
    if getattr(args, "interval", 5) < 5:
        parser.error("--interval 必须至少为 5 秒")
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
    settings = get_settings()
    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    try:
        if args.command in {"withdraw", "restore"}:
            with Session(engine) as session, session.begin():
                withdraw_video(session, args.content_id, restore=args.command == "restore")
            emit({"status": args.command, "content_item_id": str(args.content_id)})
            return 0
        if args.command == "inventory" or args.dry_run:
            with Session(engine) as session:
                emit(inventory(session, args))
            return 0
        version = f"{args.extractor_version}:{settings.deepseek_model}"
        if len(version) > 160:
            raise ValueError("抽取版本和模型名称合计不能超过 160 个字符")
        extractor = (
            DeepSeekVideoExtractor(settings, version=args.extractor_version)
            if args.command in {"run", "watch"}
            else None
        )
        failed = False
        while True:
            with Session(engine) as session, session.begin():
                scan = enqueue_videos(
                    session,
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
                result = process_next(engine, extractor, content_ids=args.content_id)
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
                "hint": "检查 PostgreSQL 连接及 alembic upgrade head；此工具不自动迁移",
            }
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
