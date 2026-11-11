#!/usr/bin/env python3
"""Build and verify source-grounded multimodal graphs from fixed technical manual pages."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.core.config import Settings  # noqa: E402
from app.services.document_graph.pipeline import run_pipeline  # noqa: E402
from app.services.document_graph.queries import retrieve, verify  # noqa: E402
from app.services.document_graph.source import prepare  # noqa: E402
from app.services.document_graph.storage import DocumentGraphStore  # noqa: E402


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--selection", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--source-root", type=Path, default=ROOT)
    run = sub.add_parser("pipeline")
    run.add_argument("--source-bundle", type=Path, required=True)
    run.add_argument("--report", type=Path, required=True)
    run.add_argument("--response-cache", type=Path, required=True)
    run.add_argument("--retry-failed", action="store_true")
    run.add_argument("--cache-only", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    for name in ["query", "verify", "withdraw", "restore"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--source-id", required=name != "query")
        if name == "query":
            cmd.add_argument("--q", default="")
            cmd.add_argument("--limit", type=int, default=10)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            b = prepare(args.selection, args.output, source_root=args.source_root)
            result = dict(
                status="prepared",
                manuals=len(b["manuals"]),
                windows=len(b["cases"]),
                source_hash=b["source_hash"],
            )
        else:
            settings = Settings()
            if args.command == "pipeline" and args.dry_run:
                result = asyncio.run(
                    run_pipeline(
                        settings,
                        None,
                        args.source_bundle,
                        args.report,
                        cache=args.response_cache,
                        dry_run=True,
                    )
                )
            else:
                with DocumentGraphStore(settings) as store:
                    if args.command == "pipeline":
                        store.initialize()
                        result = asyncio.run(
                            run_pipeline(
                                settings,
                                store,
                                args.source_bundle,
                                args.report,
                                cache=args.response_cache,
                                retry_failed=args.retry_failed,
                                cache_only=args.cache_only,
                            )
                        )
                    elif args.command == "query":
                        result = retrieve(store, args.q, source_id=args.source_id, limit=args.limit)
                    elif args.command == "verify":
                        result = verify(store, args.source_id)
                    else:
                        store.withdraw(args.source_id, restore=args.command == "restore")
                        result = dict(status=args.command, source_id=args.source_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("status") == "incomplete" or result.get("verified") is False else 0
    except Exception as exc:
        print(json.dumps(dict(status="error", error_type=type(exc).__name__), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
