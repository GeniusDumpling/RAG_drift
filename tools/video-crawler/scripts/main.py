#!/usr/bin/env python3
"""统一 CLI 入口：搜索 → 去重 → 入库。

参数来自 conf.yaml（可用命令行覆盖），API Key 来自仓库根 .env（全局）。

用法：
    # 显式指定搜索词
    uv run python3 main.py --query "drone GPS spoofing"

    # 定时采集：按 conf.yaml 主题轮换（cron/systemd 每 rotation_interval_seconds 触发一次）
    uv run python3 main.py --scheduled

    # 覆盖单次参数
    uv run python3 main.py --query "DJI" --video-limit 5 --max-results 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from config_loader import DEFAULT_CONF_PATH
from pipeline import emit_json, load_conf_with_env, run_search_and_ingest, select_rotating_entry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONF_PATH, help="conf.yaml 路径")
    parser.add_argument("--query", default=None, help="搜索词；缺省时按 conf.yaml 主题轮换")
    parser.add_argument("--scheduled", action="store_true", help="定时模式：按时间桶轮换主题")
    parser.add_argument("--max-results", type=int, default=None, help="搜索候选数（1-50）")
    parser.add_argument("--video-limit", type=int, default=None, help="最多入库视频数")
    parser.add_argument("--language", default=None, help="字幕语言优先级")
    parser.add_argument(
        "--order",
        choices=("date", "rating", "relevance", "title", "videoCount", "viewCount"),
        default=None,
        help="排序方式",
    )
    parser.add_argument("--caption-only", dest="caption_only", action="store_true", default=None)
    parser.add_argument("--no-caption-only", dest="caption_only", action="store_false")
    parser.add_argument("--whisper-fallback", action="store_true", default=None)
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        conf = load_conf_with_env(args.config)
        if args.query:
            query, query_language = args.query, None
        elif args.scheduled or "queries" in (conf.get("search") or {}):
            entry = select_rotating_entry(conf)
            query, query_language = entry["query"], entry.get("language")
        else:
            print("错误：请提供 --query 或在 conf.yaml 配置 search.queries", file=sys.stderr)
            return 1
        if args.max_results is not None and not 1 <= args.max_results <= 50:
            print("错误：--max-results 必须在 1 到 50 之间", file=sys.stderr)
            return 1
        payload: dict[str, Any] = run_search_and_ingest(
            conf,
            query=query,
            language=args.language or query_language,
            max_results=args.max_results,
            video_limit=args.video_limit,
            order=args.order,
            caption_only=args.caption_only,
            whisper_fallback=args.whisper_fallback,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    emit_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
