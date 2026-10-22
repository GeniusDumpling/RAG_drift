from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .bundle import load_bundle
from .chunking import load_budget
from .common import ROOT, write_json
from .prepare import prepare


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="iFixit local guide preparation and RAG import")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_cmd = commands.add_parser("prepare", help="生成离线数据包，不连接数据库或模型服务")
    prepare_cmd.add_argument("--input", type=Path, default=ROOT / "data/ifixit数据")
    prepare_cmd.add_argument("--output", type=Path, required=True, help="必须是尚不存在的目录")
    prepare_cmd.add_argument("--media-map", type=Path, help="人工核验过的图片映射 JSON 数组")
    for name in ("validate", "import", "index", "cleanup", "context"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", type=Path, required=True)
        if name in {"import", "index", "cleanup"}:
            command.add_argument("--database-url", help="默认使用项目 SYNC_DATABASE_URL")
            command.add_argument("--report", type=Path, help="运行报告路径，默认写入数据包目录")
        if name == "cleanup":
            command.add_argument("--qdrant-url", required=True, help="仅清理该地址的已有删除任务")
        if name == "import":
            command.add_argument(
                "--dry-run", action="store_true", help="仅校验数据包并展示导入计划"
            )
            command.add_argument(
                "--index", action="store_true", help="导入后立即索引，需提供 tokenizer"
            )
        if name == "context":
            command.add_argument("--guide-id", type=int, required=True)
            command.add_argument("--locale", default="en")
            command.add_argument("--step", type=int, help="补充指定步骤及相邻步骤；默认完整指南")
            command.add_argument("--max-chars", type=int, default=12000)
    for name in ("prepare", "import", "index"):
        command = commands.choices[name]
        command.add_argument("--tokenizer", help="与嵌入模型一致的本地 tokenizer 目录或缓存模型 ID")
        command.add_argument("--max-tokens", type=int, default=480)
        command.add_argument("--max-chars", type=int, default=1600)
    return parser.parse_args(argv)


def context(bundle: dict, document_id: str, step: int | None, max_chars: int) -> dict:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    guide = bundle["guides_by_id"].get(document_id)
    if guide is None:
        raise ValueError("guide not found")
    selected = []
    selected_media_ids = set()
    if step is not None and not any(
        s["step_order"] == step for s in bundle["steps_by_doc"][document_id]
    ):
        raise ValueError("step not found")
    used = 0
    omitted = []
    for section in guide["sections"]:
        order = section.get("step_order")
        if step is not None and order is not None and abs(order - step) > 1:
            continue
        if used + len(section["text"]) + 2 > max_chars:
            omitted.append(section["key"])
            continue
        selected.append(section["text"])
        selected_media_ids.update(section.get("media_ref_ids", []))
        used += len(section["text"]) + 2
    return {
        "document_id": document_id,
        "source_url": guide["source_url"],
        "text": "\n\n".join(selected),
        "omitted_sections": omitted,
        "prerequisites": guide["prerequisites"],
        "quality_flags": guide["quality_flags"],
        "media_refs": [
            r
            for r in guide["media_refs"]
            if r["mapping_status"] == "confirmed"
            and (r["role"] == "cover" or r["media_ref_id"] in selected_media_ids)
        ],
        "scope": "whole_guide" if step is None else "adjacent_steps",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(
                args.input,
                args.output,
                load_budget(
                    max_tokens=args.max_tokens,
                    max_chars=args.max_chars,
                    tokenizer_path=args.tokenizer,
                ),
                args.media_map,
            )
            print(
                json.dumps(
                    {
                        "output": str(args.output),
                        "status": result["status"],
                        "counts": result["counts"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if result["status"] == "success" else 2
        bundle = load_bundle(args.bundle)
        if args.command == "validate" or (args.command == "import" and args.dry_run):
            print(
                json.dumps(
                    {
                        "status": bundle["manifest"]["status"],
                        "validated": True,
                        "writes": False,
                        "counts": bundle["manifest"]["counts"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if bundle["manifest"]["status"] == "success" else 2
        if args.command == "context":
            print(
                json.dumps(
                    context(
                        bundle, f"ifixit:{args.guide_id}:{args.locale}", args.step, args.max_chars
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        budget = None
        if args.command == "index" or getattr(args, "index", False):
            if not args.tokenizer:
                raise ValueError(
                    "--tokenizer is required for vector indexing; no network auto-download"
                )
            budget = load_budget(
                max_tokens=args.max_tokens, max_chars=args.max_chars, tokenizer_path=args.tokenizer
            )
        # Backend modules and settings are deliberately lazy: prepare, validate and
        # dry-run work without database credentials or an installed database driver.
        from .database import (
            cleanup_bundle,
            configured_engine,
            import_bundle,
            index_bundle,
            locked_session,
        )

        engine, indexer = configured_engine(args.database_url, with_index=budget is not None)
        result = {}
        try:
            with locked_session(engine) as session:
                if args.command == "cleanup":
                    result["cleanup"] = cleanup_bundle(session, bundle, args.qdrant_url)
                if args.command == "import":
                    result["import"] = import_bundle(session, bundle)
                if budget is not None:
                    assert indexer is not None
                    result["index"] = index_bundle(session, bundle, indexer, budget)
        finally:
            engine.dispose()
        result["status"] = (
            "success" if all(r["status"] == "success" for r in result.values()) else "partial"
        )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        report = args.report or args.bundle / f"{args.command}-report-{stamp}.json"
        write_json(report, result)
        print(json.dumps({"report": str(report), **result}, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "success" else 2
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:2000]},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
