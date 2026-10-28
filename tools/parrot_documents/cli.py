"""Explicit offline/remote stages; source files are never rewritten."""

import argparse
import asyncio
from pathlib import Path

from tools.drone_documents.common import progress, read_json, validate_bundle, write_json

from .catalog import Catalog
from .prepare import build_chunks, prepare, quality
from .report import report


def require_parrot(root):
    manifest = validate_bundle(root)
    if manifest.get("manufacturer") != "Parrot":
        raise ValueError("该命令只接受 Parrot 数据包")
    return manifest


async def enrich_bundle(root, concurrency):
    from app.core.config import get_settings

    from tools.drone_documents.caption_quality import QUALITY_VERSION
    from tools.drone_documents.common import digest
    from tools.drone_documents.enrich import PROMPT_VERSION, SYSTEM, enrich

    manifest = require_parrot(root)
    config = manifest["config"]
    if config.get("figures") and (
        config.get("figure_model") != get_settings().vlm_model
        or config.get("figure_prompt_hash") != digest(SYSTEM.replace("DJI", "Parrot").encode())
        or config.get("figure_prompt_version") != PROMPT_VERSION
        or config.get("caption_quality_version") != QUALITY_VERSION
    ):
        raise ValueError("图示模型或提示词已变化，请创建新数据包")
    result = await enrich(
        root,
        concurrency=concurrency,
        chunk_builder=build_chunks,
        quality_builder=quality,
        system_prompt=SYSTEM.replace("DJI", "Parrot"),
    )
    report(root)
    return result


def parser():
    result = argparse.ArgumentParser(description="Parrot PDF / SDK HTML 解析与独立文档库入库")
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in [
        "list-scopes",
        "prepare",
        "validate",
        "report",
        "enrich",
        "bootstrap",
        "import",
        "index",
        "query",
        "evaluate",
        "run",
    ]:
        sub = subparsers.add_parser(name)
        if name in {"list-scopes", "prepare", "run"}:
            sub.add_argument("--input", type=Path, default=Path("data/Parrot无人机文档"))
        if name not in {"list-scopes", "bootstrap"}:
            sub.add_argument("--bundle", type=Path, required=True)
        if name in {"prepare", "run"}:
            sub.add_argument("--scope", action="append", help="可重复；省略则处理全部可用资料")
            sub.add_argument("--kind", choices=["all", "pdf", "html"], default="all")
            sub.add_argument("--document-type", action="append")
            sub.add_argument("--limit", type=int, help="最多处理多少份去重资料，用于抽查")
            sub.add_argument("--ocr", choices=["auto", "off"], default="auto")
            sub.add_argument("--no-figures", action="store_true", help="不裁剪 PDF 图示")
            sub.add_argument("--no-page-renders", action="store_true")
            sub.add_argument("--chunk-max-chars", type=int, default=2000)
            sub.add_argument("--resume", action="store_true", help="按已校验的文档检查点恢复解析")
        if name in {"enrich", "run"}:
            sub.add_argument("--concurrency", type=int, default=3)
        if name in {"bootstrap", "import", "index", "query", "evaluate", "run"}:
            sub.add_argument("--database-url")
            sub.add_argument("--qdrant-url")
            sub.add_argument("--collection")
        if name == "query":
            sub.add_argument("--question", required=True)
        if name in {"query", "evaluate"}:
            sub.add_argument("--top-k", type=int, default=5)
        if name == "evaluate":
            sub.add_argument(
                "--questions-file",
                type=Path,
                required=True,
                help="中文/英文问题的 JSON 字符串数组；验证真实召回，不生成答案",
            )
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if getattr(args, "concurrency", 1) < 1 or getattr(args, "top_k", 1) < 1:
            raise ValueError("concurrency 和 top-k 必须为正整数")
        if args.command == "list-scopes":
            progress("scopes", scopes=Catalog(args.input).summaries())
            return 0
        if args.command in {"prepare", "run"}:
            prepare(
                args.input,
                args.bundle,
                scopes=args.scope,
                kind=args.kind,
                types=args.document_type,
                limit=args.limit,
                ocr=args.ocr,
                figures=not args.no_figures,
                render_pages=not args.no_page_renders,
                chunk_max_chars=args.chunk_max_chars,
                resume=args.resume,
            )
            report(args.bundle)
        elif args.command != "bootstrap":
            require_parrot(args.bundle)
        if args.command == "validate":
            progress("validated", manifest=require_parrot(args.bundle)["stage"])
        if args.command == "report":
            progress("report", **report(args.bundle))
        if args.command in {"enrich", "run"}:
            result = asyncio.run(enrich_bundle(args.bundle, args.concurrency))
            if result["enrichment_failures"]:
                return 2
        if args.command in {"bootstrap", "import", "index", "query", "evaluate", "run"}:
            from tools.drone_documents.storage import bootstrap, import_bundle, index_bundle, query

            remote = {
                "database_url": args.database_url,
                "qdrant_url": args.qdrant_url,
                "collection": args.collection,
            }
            if args.command in {"bootstrap", "run"}:
                progress("bootstrap", **bootstrap(args.database_url))
            if args.command in {"import", "run"}:
                import_bundle(
                    args.bundle, database_url=args.database_url, collection=args.collection
                )
            if args.command in {"index", "run"}:
                if require_parrot(args.bundle)["stage"] != "enriched":
                    raise ValueError("请先完成 enrich，再建立向量索引")
                result = asyncio.run(index_bundle(args.bundle, **remote))
                if result["failures"]:
                    return 2
            if args.command in {"query", "evaluate"}:
                questions = (
                    [args.question] if args.command == "query" else read_json(args.questions_file)
                )
                if (
                    not isinstance(questions, list)
                    or not questions
                    or any(not isinstance(q, str) or not q.strip() for q in questions)
                ):
                    raise ValueError("问题文件须为非空 JSON 字符串数组")
                results = [
                    asyncio.run(query(args.bundle, q, top_k=args.top_k, **remote))
                    for q in questions
                ]
                write_json(args.bundle / "retrieval-report.json", {"queries": results})
                for item in results:
                    progress("query", **item)
                if any(not item["evidence"] for item in results):
                    return 2
        return 0
    except Exception as exc:
        details = {"error_type": type(exc).__name__}
        if isinstance(exc, ValueError | FileNotFoundError):
            details["message"] = str(exc)
        progress("error", **details)
        return 1
