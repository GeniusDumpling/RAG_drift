#!/usr/bin/env python3
"""Bounded forum knowledge pipeline: assess, extract, review, publish and verify."""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.core.config import Settings  # noqa: E402
from app.services.forum_graph import queries  # noqa: E402
from app.services.forum_graph.pipeline import plan, run_pipeline  # noqa: E402
from app.services.forum_graph.source import inventory, load_material, save_json  # noqa: E402
from app.services.forum_graph.storage import ForumGraphStore  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=Path)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("inventory", "plan", "pipeline", "watch"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--content-id", type=UUID, action="append")
        cmd.add_argument("--forums", nargs="+", choices=["ardupilot", "px4"])
        cmd.add_argument("--limit", type=int, default=20)
        if name != "inventory":
            cmd.add_argument("--manifest", type=Path, required=name != "watch")
        if name in {"pipeline", "watch"}:
            cmd.add_argument("--report", type=Path, required=name == "pipeline")
            cmd.add_argument("--response-cache", type=Path, required=True)
            cmd.add_argument("--retry-failed", action="store_true")
            cmd.add_argument("--cache-only", action="store_true")
            cmd.add_argument("--dry-run", action="store_true")
        if name == "watch":
            cmd.add_argument("--interval", type=int, default=300)
            cmd.add_argument("--once", action="store_true")
    for name in ("query", "verify", "status", "withdraw", "restore"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--source-id", required=name != "query")
        if name == "query":
            cmd.add_argument("--q", default="")
            cmd.add_argument("--limit", type=int, default=20)
            cmd.add_argument("--offset", type=int, default=0)
        if name == "verify":
            cmd.add_argument("--output", type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    engine = None
    try:
        if args.env_file and not args.env_file.is_file():
            raise ValueError("显式配置不存在，拒绝回退")
        settings = (
            Settings(_env_file=(ROOT / ".env", args.env_file)) if args.env_file else Settings()
        )
        if getattr(args, "limit", 1) < 1 or getattr(args, "interval", 1) < 1:
            raise ValueError("限额与间隔必须为正")
        engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
        if args.command == "inventory":
            rows = []
            with Session(engine) as session:
                for cid in inventory(session, forums=args.forums, content_ids=args.content_id)[
                    : args.limit
                ]:
                    try:
                        m = load_material(session, cid)
                        rows.append(
                            dict(
                                content_item_id=str(cid),
                                source_id=m.source_id,
                                posts=len(m.posts),
                                chunks=sum(len(p.chunks) for p in m.posts),
                                fingerprint=m.fingerprint,
                                status="ready",
                            )
                        )
                    except ValueError as exc:
                        rows.append(
                            dict(content_item_id=str(cid), status="waiting", reason=str(exc))
                        )
            result = dict(status="read_only", items=rows)
        else:
            with ForumGraphStore(settings) as store:
                if args.command in {"plan", "pipeline", "watch"}:
                    for name in ("manifest", "report", "response_cache"):
                        path = getattr(args, name, None)
                        if path and not path.resolve().is_relative_to(
                            settings.forum_graph_artifacts_dir.resolve()
                        ):
                            raise ValueError("计划、报告和缓存须位于 FORUM_GRAPH_ARTIFACTS_DIR 内")
                    if args.command == "plan":
                        if args.manifest.exists():
                            raise ValueError("新计划不能覆盖已有文件")
                        result = plan(
                            settings,
                            store,
                            engine,
                            limit=args.limit,
                            content_ids=args.content_id,
                            forums=args.forums,
                        )
                        save_json(args.manifest, result)
                    else:
                        if not args.dry_run:
                            store.initialize()
                        counter = 0
                        while True:
                            key = str(time.time_ns())
                            manifest = (
                                args.manifest
                                or settings.forum_graph_artifacts_dir / f"watch-{key}.plan.json"
                            )
                            report = (
                                args.report
                                or settings.forum_graph_artifacts_dir / f"watch-{key}.report.json"
                            )
                            result = asyncio.run(
                                run_pipeline(
                                    settings,
                                    store,
                                    engine,
                                    manifest=manifest,
                                    report=report,
                                    cache=args.response_cache,
                                    limit=args.limit,
                                    content_ids=args.content_id,
                                    forums=args.forums,
                                    retry_failed=args.retry_failed and counter == 0,
                                    cache_only=args.cache_only,
                                    dry_run=args.dry_run,
                                )
                            )
                            counter += 1
                            if args.command != "watch" or args.once or args.dry_run:
                                break
                            print(json.dumps(result, ensure_ascii=False), flush=True)
                            # Explicit watch runs continuously; no timer is installed implicitly.
                            time.sleep(args.interval)
                elif args.command in {"query", "verify"}:
                    with Session(engine) as session:
                        result = (
                            queries.retrieve(
                                store,
                                session,
                                args.q,
                                source_id=args.source_id,
                                limit=args.limit,
                                offset=args.offset,
                            )
                            if args.command == "query"
                            else queries.verify(store, session, args.source_id)
                        )
                        if args.command == "verify" and args.output and result["verified"]:
                            if not args.output.resolve().is_relative_to(
                                settings.forum_graph_artifacts_dir.resolve()
                            ):
                                raise ValueError("复核结果须位于 FORUM_GRAPH_ARTIFACTS_DIR 内")
                            review = queries.review_bundle(store, session, args.source_id)
                            save_json(args.output, review)
                            import html

                            rendered = html.escape(json.dumps(review, ensure_ascii=False, indent=2))
                            args.output.with_suffix(".html").write_text(
                                '<!doctype html><meta charset="utf-8">'
                                "<title>论坛图谱完整复核</title>"
                                "<h1>论坛图谱完整复核</h1><pre>" + rendered + "</pre>",
                                encoding="utf-8",
                            )
                            result["review_output"] = str(args.output)
                elif args.command == "status":
                    result = queries.status_summary(store, args.source_id)
                else:
                    store.withdraw(args.source_id, restore=args.command == "restore")
                    result = dict(status=args.command, source_id=args.source_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 1 if result.get("status") == "incomplete" or result.get("verified") is False else 0
    except Exception as exc:
        print(json.dumps(dict(status="error", error_type=type(exc).__name__), ensure_ascii=False))
        return 1
    finally:
        if engine:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
