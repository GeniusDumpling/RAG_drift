#!/usr/bin/env python3
"""Run fixed manual windows with durable exact response caches (no graph publication)."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
from app.core.config import Settings  # noqa: E402
from app.services.document_graph.client import VisionClient  # noqa: E402
from app.services.document_graph.extraction import extract, policy_id  # noqa: E402
from app.services.document_graph.grounding import validate_result  # noqa: E402
from app.services.document_graph.source import (  # noqa: E402
    ensure_report_path,
    load_bundle,
    read_json,
    save_json,
)


async def run(args):
    bundle = load_bundle(args.source_bundle)
    ensure_report_path(args.source_bundle, args.output)
    settings = Settings()
    policy = policy_id(settings)
    data = dict(
        source_hash=bundle["source_hash"],
        policy=policy,
        results={},
        model_profile={
            "llm": settings.llm_model,
            "vlm": settings.document_graph_vlm_model or settings.vlm_model,
        },
    )
    if args.output.exists():
        previous = read_json(args.output)
        if previous["source_hash"] != bundle["source_hash"] or previous.get("policy") != policy:
            raise ValueError("输出来源或抽取规则已变，请使用新文件")
        data = previous
    semaphore = asyncio.Semaphore(args.workers)

    async def one(case):
        async with semaphore:
            prior = data["results"].get(case["id"])
            if prior and (prior["status"] == "success" or not args.retry_errors):
                if prior["status"] == "success":
                    validate_result(bundle, case, prior["output"], policy, root=args.source_bundle)
                return
            client = VisionClient(settings, args.response_cache, cache_only=args.cache_only)
            print(json.dumps(dict(case=case["id"], status="running")), flush=True)
            try:
                output = await extract(bundle, case, client, args.source_bundle)
                row = dict(status="success", output=output)
            except Exception as exc:
                row = dict(status="error", error_type=type(exc).__name__)
                if isinstance(exc, ValueError | RuntimeError):
                    row["diagnostic"] = str(exc)[:1600]
            row.update(trace=client.trace, usage=client.usage.snapshot())
            data["results"][case["id"]] = row
            save_json(args.output, data)
            print(
                json.dumps(
                    dict(
                        case=case["id"],
                        status=row["status"],
                        facts=len(row.get("output", {}).get("facts", [])),
                        error_type=row.get("error_type"),
                    )
                ),
                flush=True,
            )

    await asyncio.gather(*(one(c) for c in bundle["cases"]))
    return int(any(row["status"] != "success" for row in data["results"].values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--response-cache", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--cache-only", action="store_true")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
