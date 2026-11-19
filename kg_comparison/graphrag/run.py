#!/usr/bin/env python3
"""Step 3: Run GraphRAG baselines (community detection disabled) on RAG_drift datasets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.core.llm_config import LLMSettings  # noqa: E402

from kg_comparison.evaluate_baseline import (  # noqa: E402
    DEFAULT_RELATION_DATASET,
    atomic_write_json,
    evaluate_payload,
    render_evaluation_report,
    stable_hash,
)
from kg_comparison.graphrag.engine import GraphRAGAdapter, GraphRAGConfig  # noqa: E402
from kg_comparison.llm_client import ComparisonLLMClient, DiskResponseCache  # noqa: E402
from kg_comparison.schema import (  # noqa: E402
    FORMAT_VERSION,
    BaselineRunPayload,
    CasePrediction,
    CaseUsage,
)


def _load_dataset(
    dataset_path: Path, case_ids: list[str] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise ValueError(f"{dataset_path}: 数据集必须包含 cases 列表")
    cases: list[dict[str, Any]] = data["cases"]
    if case_ids:
        selected = set(case_ids)
        unknown = selected - {str(c.get("id")) for c in cases}
        if unknown:
            raise ValueError("未知 case-id: " + ", ".join(sorted(unknown)))
        cases = [c for c in cases if str(c.get("id")) in selected]
    has_relations = bool(cases and "relations" in cases[0])
    return data, cases, has_relations


def build_config(
    *,
    mode: str,
    granularity: str,
    backend: str,
    max_gleanings: int,
    summarize_descriptions: bool,
    with_assertion: bool,
    request_quote: bool,
    input_scope: str,
) -> GraphRAGConfig:
    return GraphRAGConfig(
        mode=mode,  # type: ignore[arg-type]
        granularity=granularity,  # type: ignore[arg-type]
        backend=backend,  # type: ignore[arg-type]
        max_gleanings=max_gleanings,
        summarize_descriptions=summarize_descriptions,
        with_assertion=with_assertion,
        request_quote=request_quote,
        input_scope=input_scope,  # type: ignore[arg-type]
    )


def _method_version(config: GraphRAGConfig) -> str:
    return (
        f"graphrag-{config.backend}-{config.mode}"
        f"-glean{config.max_gleanings}"
        f"-sum{int(config.summarize_descriptions)}"
        f"-assert{int(config.with_assertion)}"
    )


def run_experiment(
    *,
    dataset_path: Path = DEFAULT_RELATION_DATASET,
    output_path: Path | None = None,
    eval_output_path: Path | None = None,
    eval_report_path: Path | None = None,
    config: GraphRAGConfig | None = None,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
    timeout: float = 120.0,
    reasoning_effort: str = "none",
    response_cache_dir: Path | None = None,
    cache_only: bool = False,
    case_ids: list[str] | None = None,
    workers: int = 2,
    retry_errors: bool = False,
    dry_run: bool = False,
    client: Any = None,
) -> tuple[BaselineRunPayload | dict[str, Any], dict[str, Any] | None]:
    """Run GraphRAG extraction (and optional Element Merge + evaluation) over a dataset."""
    cfg = config or GraphRAGConfig()
    if not 1 <= workers <= 4:
        raise ValueError("workers 必须为 1–4")
    if cache_only and response_cache_dir is None and client is None:
        raise ValueError("--cache-only 需要指定 --response-cache")

    dataset, cases, has_relations = _load_dataset(dataset_path, case_ids)
    ds_version = str(dataset.get("version") or dataset_path.stem)
    ds_sha256 = stable_hash(dataset)
    gold_entities = sum(len(c.get("entities", [])) for c in cases)
    gold_relations = sum(len(c.get("relations", [])) for c in cases) if has_relations else 0
    method = f"graphrag_{cfg.mode}"
    method_version = _method_version(cfg)

    protected = {dataset_path.resolve()}
    for target in (output_path, eval_output_path, eval_report_path):
        if target is not None and target.resolve() in protected:
            raise ValueError(f"输出路径不能覆盖输入数据集: {target}")

    if dry_run:
        calls_per_case = 1 + cfg.max_gleanings + (1 if cfg.mode == "open_mapped" else 0)
        summary = {
            "method": method,
            "method_version": method_version,
            "mode": cfg.mode,
            "granularity": cfg.granularity,
            "backend": cfg.backend,
            "community_detection_enabled": False,
            "max_gleanings": cfg.max_gleanings,
            "summarize_descriptions": cfg.summarize_descriptions,
            "with_assertion": cfg.with_assertion,
            "request_quote": cfg.effective_request_quote,
            "input_scope": cfg.input_scope,
            "dataset_version": ds_version,
            "cases": len(cases),
            "gold_entities": gold_entities,
            "gold_relations": gold_relations,
            "estimated_max_extraction_calls": calls_per_case * len(cases),
            "model_called": False,
        }
        return summary, None

    if output_path is None:
        raise ValueError("非 dry-run 模式下必须指定 --output")

    if cfg.backend == "library" and importlib.util.find_spec("graphrag") is None:
        raise ImportError(
            "未安装官方 `graphrag` 包。请先安装 `graphrag`，或使用默认 `--backend native`。"
        )

    settings = LLMSettings()
    resolved_model = model or settings.llm_model
    resolved_base_url = base_url or settings.base_url
    if client is None and cfg.backend == "native":
        cache = (
            DiskResponseCache(response_cache_dir, cache_only=cache_only)
            if response_cache_dir
            else None
        )
        client = ComparisonLLMClient(
            base_url=resolved_base_url,
            api_key=settings.llm_api_key,
            model=resolved_model,
            temperature=temperature,
            timeout_seconds=timeout,
            reasoning_effort=reasoning_effort,
            cache=cache,
        )

    adapter = GraphRAGAdapter(cfg, client=client)

    payload = BaselineRunPayload(
        format_version=FORMAT_VERSION,
        method=method,
        method_version=method_version,
        mode=cfg.mode,
        granularity=cfg.granularity,
        model=resolved_model,
        temperature=temperature,
        input_scope=cfg.input_scope,
        dataset_version=ds_version,
        dataset_sha256=ds_sha256,
    )

    if output_path.exists():
        saved = BaselineRunPayload.model_validate(
            json.loads(output_path.read_text(encoding="utf-8"))
        )
        mismatch_fields = [
            key
            for key in (
                "method",
                "method_version",
                "mode",
                "granularity",
                "dataset_sha256",
                "input_scope",
            )
            if getattr(saved, key) != getattr(payload, key)
        ]
        if mismatch_fields:
            raise ValueError(
                f"输出文件属于其他实验配置 ({', '.join(mismatch_fields)})，请改用新文件名"
            )
        payload = saved

    pending = [
        case
        for case in cases
        if str(case["id"]) not in payload.results
        or (retry_errors and payload.results[str(case["id"])].status == "error")
    ]

    def _run_one(case: dict[str, Any]) -> CasePrediction:
        started = time.perf_counter()
        try:
            return adapter.extract_case(case)
        except Exception as exc:
            return CasePrediction(
                status="error",
                seconds=round(time.perf_counter() - started, 4),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    def _save_payload() -> None:
        payload.updated_at = datetime.now(UTC).isoformat()
        atomic_write_json(output_path, payload.model_dump())

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one, case): str(case["id"]) for case in pending}
            for future in as_completed(futures):
                cid = futures[future]
                payload.results[cid] = future.result()
                _save_payload()
                print(
                    json.dumps(
                        {"case_id": cid, "status": payload.results[cid].status},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    if cfg.granularity == "corpus_batch" and cfg.backend == "native":
        cases_by_id = {str(c["id"]): c for c in cases}
        has_success = any(
            payload.results.get(cid) and payload.results[cid].status == "success"
            for cid in cases_by_id
        )
        if has_success and (pending or not payload.corpus_stats):
            merge_started = time.perf_counter()
            corpus_usage = CaseUsage()
            subset = {
                cid: payload.results[cid] for cid in cases_by_id if cid in payload.results
            }
            updated_results, corpus_stats = adapter.merge_corpus(
                cases_by_id, subset, corpus_usage
            )
            payload.results.update(updated_results)
            payload.corpus_usage = corpus_usage
            payload.corpus_seconds = round(time.perf_counter() - merge_started, 4)
            payload.corpus_stats = corpus_stats
            _save_payload()

    if not output_path.exists():
        _save_payload()

    eval_report: dict[str, Any] | None = None
    if eval_output_path is not None or eval_report_path is not None:
        eval_report = evaluate_payload(
            payload,
            cases,
            dataset_version=ds_version,
            dataset_sha256=ds_sha256,
            has_relations=has_relations,
        )
        if eval_output_path is not None:
            atomic_write_json(eval_output_path, eval_report)
        if eval_report_path is not None:
            eval_report_path.parent.mkdir(parents=True, exist_ok=True)
            eval_report_path.write_text(
                render_evaluation_report(cases, eval_report), encoding="utf-8"
            )

    return payload, eval_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=["open", "open_mapped", "constrained"], default="open"
    )
    parser.add_argument(
        "--granularity", choices=["per_chunk", "corpus_batch"], default="per_chunk"
    )
    parser.add_argument("--backend", choices=["native", "library"], default="native")
    parser.add_argument(
        "--max-gleanings",
        type=int,
        default=1,
        help="GraphRAG 召回补充轮次 (0-3，默认 1)",
    )
    parser.add_argument(
        "--summarize-descriptions",
        action="store_true",
        help="corpus_batch 合并跨 TextUnit 实体/边时用 LLM 汇总描述",
    )
    parser.add_argument(
        "--with-assertion",
        action="store_true",
        help="constrained / open_mapped 模式生效：同时预测 assertion 与 quote",
    )
    parser.add_argument(
        "--request-quote",
        action="store_true",
        help="额外要求模型输出关系引文 quote",
    )
    parser.add_argument(
        "--input-scope",
        choices=["title_and_current_chunk_only", "chunk_only"],
        default="title_and_current_chunk_only",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_RELATION_DATASET)
    parser.add_argument("--output", type=Path, help="预测结果 JSON 输出路径")
    parser.add_argument("--eval-output", type=Path, help="同时生成评测结果 JSON 的路径")
    parser.add_argument("--eval-report", type=Path, help="同时生成评测 Markdown 报告的路径")
    parser.add_argument("--response-cache", type=Path, help="请求级磁盘缓存目录")
    parser.add_argument(
        "--cache-only", action="store_true", help="仅从磁盘缓存重放，不发起网络请求"
    )
    parser.add_argument("--model", type=str, help="覆盖默认 LLM_MODEL")
    parser.add_argument("--base-url", type=str, help="覆盖默认 BASE_URL")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--reasoning-effort", default="none")
    parser.add_argument("--case-id", action="append", help="仅运行指定 case ID（可重复指定）")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.dry_run and args.output is None:
        parser.error("非 --dry-run 模式下必须指定 --output")
    if not 1 <= args.workers <= 4:
        parser.error("--workers 必须为 1–4")
    if not 0 <= args.max_gleanings <= 3:
        parser.error("--max-gleanings 必须为 0–3")
    if args.cache_only and not args.response_cache:
        parser.error("--cache-only 需要同时指定 --response-cache")

    cfg = build_config(
        mode=args.mode,
        granularity=args.granularity,
        backend=args.backend,
        max_gleanings=args.max_gleanings,
        summarize_descriptions=args.summarize_descriptions,
        with_assertion=args.with_assertion,
        request_quote=args.request_quote,
        input_scope=args.input_scope,
    )

    try:
        result, eval_report = run_experiment(
            dataset_path=args.dataset,
            output_path=args.output,
            eval_output_path=args.eval_output,
            eval_report_path=args.eval_report,
            config=cfg,
            model=args.model,
            base_url=args.base_url,
            temperature=args.temperature,
            timeout=args.timeout,
            reasoning_effort=args.reasoning_effort,
            response_cache_dir=args.response_cache,
            cache_only=args.cache_only,
            case_ids=args.case_id,
            workers=args.workers,
            retry_errors=args.retry_errors,
            dry_run=args.dry_run,
        )
    except (ValueError, ImportError) as exc:
        parser.error(str(exc))

    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    assert isinstance(result, BaselineRunPayload)
    if eval_report is not None:
        print(json.dumps(eval_report["metrics"], ensure_ascii=False, indent=2))
    else:
        errors = [cid for cid, p in result.results.items() if p.status != "success"]
        print(
            json.dumps(
                {
                    "method": result.method,
                    "mode": result.mode,
                    "granularity": result.granularity,
                    "cases": len(result.results),
                    "errors": errors,
                    "corpus_stats": result.corpus_stats,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    has_errors = any(p.status != "success" for p in result.results.values())
    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
