import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from .catalog import Catalog
from .common import progress, validate_bundle, write_json
from .enrich import enrich
from .evaluate import evaluate, verify_answers
from .extract import DOCUMENT_TYPES, prepare
from .questions import evaluation_questions
from .report import report
from .storage import bootstrap, import_bundle, index_bundle, query


def main() -> int:
    parser = argparse.ArgumentParser(description="DJI 中文文档解析、图示描述与独立数据库入库")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ["batch-plan", "batch-run", "batch-start", "batch-finalize", "_batch-stage"]:
        sub = subparsers.add_parser(command)
        sub.add_argument("--batch-root", type=Path, required=True)
        if command == "batch-plan":
            sub.add_argument("--input", type=Path, default=Path("data/DJI无人机文档"))
            sub.add_argument("--models", nargs="+")
            sub.add_argument("--exclude", nargs="*", default=["air-2s"])
        if command in {"batch-run", "batch-start"}:
            sub.add_argument("--parse-workers", type=int, default=2)
            sub.add_argument("--enrich-workers", type=int, default=2)
            sub.add_argument("--concurrency", type=int, default=3)
        if command == "_batch-stage":
            sub.add_argument("--job-id", required=True)
            sub.add_argument("--stage", choices=["prepare", "enrich", "index"], required=True)
            sub.add_argument("--concurrency", type=int, default=3)
    for command in [
        "list-models",
        "prepare",
        "enrich",
        "validate",
        "bootstrap",
        "import",
        "index",
        "query",
        "report",
        "run",
        "evaluate",
        "verify-answers",
    ]:
        sub = subparsers.add_parser(command)
        if command not in {"bootstrap", "list-models"}:
            sub.add_argument(
                "--bundle",
                type=Path,
                required=True,
                help="prepare/run 时为新输出目录，其他命令为已有数据包",
            )
        if command in {"prepare", "run", "list-models"}:
            sub.add_argument("--input", type=Path, default=Path("data/DJI无人机文档"))
        if command in {"prepare", "run"}:
            sub.add_argument("--model", required=True, help="下载索引中的机型 slug")
            sub.add_argument("--skip-specs", action="store_true", help="只处理 PDF")
            sub.add_argument("--ocr", choices=["auto", "off"], default="auto")
            sub.add_argument(
                "--document-type",
                action="append",
                choices=sorted(DOCUMENT_TYPES),
                help="可重复，筛选 PDF 类型；技术参数仍由 --skip-specs 控制",
            )
        if command in {"evaluate", "run"}:
            sub.add_argument("--questions-file", type=Path, help="可选的 JSON 问题字符串数组")
        if command == "evaluate":
            sub.add_argument("--baseline-bundle", type=Path, help="对照旧包答案的关键条件遗漏")
        if command in {"enrich", "run"}:
            sub.add_argument("--concurrency", type=int, default=3)
            sub.add_argument(
                "--reuse-descriptions-from",
                type=Path,
                help="复用旧包候选说明，但必须经过新质量检查",
            )
        if command in {"bootstrap", "import", "index", "query", "run", "evaluate"}:
            sub.add_argument("--database-url", default=None)
            sub.add_argument("--collection", default=None)
            sub.add_argument("--qdrant-url", default=None)
        if command == "query":
            sub.add_argument("--question", required=True)
            sub.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.command.startswith("batch-") or args.command == "_batch-stage":
            from .bulk import create_plan, run_batch, seed_cache, stage_worker, start_batch

            if args.command == "batch-plan":
                plan = create_plan(args.input, args.batch_root, args.models, args.exclude)
                seed_cache(
                    args.batch_root,
                    Path("data/processed/drone-documents/air-2s-quality-20261027-v4"),
                )
                progress(
                    "batch_planned",
                    models=len(plan["configuration"]["models"]),
                    jobs=len(plan["jobs"]),
                    pdfs=plan["unique_pdfs"],
                    pages=plan["source_pages"],
                )
                return 0
            if args.command == "_batch-stage":
                return stage_worker(args.batch_root, args.job_id, args.stage, args.concurrency)
            if args.command == "batch-start":
                result = start_batch(
                    args.batch_root, args.parse_workers, args.enrich_workers, args.concurrency
                )
                progress("batch_started", **result)
                return 0
            if args.command == "batch-finalize":
                from .bulk_finalize import finalize_batch

                try:
                    result = asyncio.run(finalize_batch(args.batch_root))
                except Exception as exc:
                    from .common import now, read_json

                    state = read_json(args.batch_root / "status.json")
                    state.update(
                        {
                            "status": "finalization_failed",
                            "updated_at": now(),
                            "error_type": type(exc).__name__,
                        }
                    )
                    write_json(args.batch_root / "status.json", state)
                    raise
                progress("batch_finalized", **result)
                return 0 if result["complete"] else 2
            result = asyncio.run(
                run_batch(
                    args.batch_root,
                    parse_workers=args.parse_workers,
                    enrich_workers=args.enrich_workers,
                    concurrency=args.concurrency,
                )
            )
            if result["status"] in {"complete", "finalizing"}:
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "tools.drone_documents",
                        "batch-finalize",
                        "--batch-root",
                        str(args.batch_root.resolve()),
                    ]
                ).returncode
            return 2
        if getattr(args, "concurrency", 1) < 1 or getattr(args, "top_k", 1) < 1:
            raise ValueError("concurrency 和 top-k 必须为正整数")
        if args.command == "list-models":
            progress("models", models=Catalog(args.input).summaries())
        if args.command in {"prepare", "run"}:
            prepare(
                args.input,
                args.bundle,
                args.model,
                include_specs=not args.skip_specs,
                ocr=args.ocr,
                document_types=args.document_type,
            )
        if args.command in {"enrich", "run"}:
            result = asyncio.run(
                enrich(
                    args.bundle,
                    concurrency=args.concurrency,
                    reuse_from=args.reuse_descriptions_from,
                )
            )
            if result["enrichment_failures"]:
                report(args.bundle)
                return 2
        if args.command in {"bootstrap", "run"}:
            bootstrap(args.database_url)
        if args.command in {"import", "run"}:
            import_bundle(args.bundle, database_url=args.database_url, collection=args.collection)
        if args.command in {"index", "run"}:
            result = asyncio.run(
                index_bundle(
                    args.bundle,
                    database_url=args.database_url,
                    qdrant_url=args.qdrant_url,
                    collection=args.collection,
                )
            )
            if result["failures"]:
                return 2
        if args.command in {"query", "run"}:
            questions = (
                [args.question]
                if args.command == "query"
                else evaluation_questions(args.bundle, args.questions_file)
            )
            results = [
                asyncio.run(
                    query(
                        args.bundle,
                        question,
                        database_url=args.database_url,
                        qdrant_url=args.qdrant_url,
                        collection=args.collection,
                        top_k=args.top_k if args.command == "query" else 5,
                    )
                )
                for question in questions
            ]
            write_json(args.bundle / "retrieval-report.json", {"queries": results})
            for result in results:
                progress("query", **result)
        if args.command == "validate":
            manifest = validate_bundle(args.bundle)
            progress("validated", stage=manifest["stage"], model=manifest["model_slug"])
        if args.command == "verify-answers":
            result = verify_answers(args.bundle)
            progress("answers_verified", **result)
            report(args.bundle)
            return 0 if result["all_answers_verified"] else 2
        if args.command == "evaluate":
            result = asyncio.run(
                evaluate(
                    args.bundle,
                    evaluation_questions(args.bundle, args.questions_file),
                    database_url=args.database_url,
                    qdrant_url=args.qdrant_url,
                    collection=args.collection,
                    baseline_root=args.baseline_bundle,
                )
            )
            progress("evaluated", **result)
            if not result["all_answers_verified"]:
                report(args.bundle)
                return 2
        if args.command in {"report", "query", "run", "evaluate"}:
            result = report(args.bundle)
            progress("report", preview=result["preview"], report=result["report"])
        return 0
    except Exception as exc:
        details = {"error_type": type(exc).__name__}
        if isinstance(exc, ValueError | FileNotFoundError):
            details["message"] = str(exc)
        if getattr(exc, "sqlstate", None):
            details["sqlstate"] = exc.sqlstate
            details["message"] = str(exc).splitlines()[0]
        progress("error", **details)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
