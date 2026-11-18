"""Run with python -m tools.autel_documents."""

import argparse
import fcntl
import json
from pathlib import Path

from .collector import Client, Collector, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description="采集 Autel 公开官方 PDF、产品页面和技术参数")
    parser.add_argument(
        "command",
        choices=["collect", "discover", "download", "verify", "report", "reindex", "build-catalog"],
    )
    parser.add_argument("--output", type=Path, default=Path("data/Autel无人机文档"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.3, help="同一主机请求最短间隔秒数")
    parser.add_argument("--max-pages", type=int, default=400)
    parser.add_argument("--max-documents", type=int)
    parser.add_argument("--max-pdf-mb", type=int, default=150)
    parser.add_argument("--seed", action="append", default=[], help="增加官方页面入口，可重复")
    parser.add_argument("--scope", action="append", default=[], help="仅下载指定型号/系列，可重复")
    parser.add_argument("--refresh", action="store_true", help="重新获取网页/PDF；旧原始文件保留")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 8 or args.delay < 0 or args.timeout <= 0 or args.retries < 0:
        parser.error("workers 须在 1–8，delay/retries 非负，timeout 为正")
    if (
        args.max_pages <= 0
        or args.max_pdf_mb <= 0
        or (args.max_documents is not None and args.max_documents <= 0)
    ):
        parser.error("采集数量及体积上限须为正数")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / ".collector.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("该输出目录已有采集任务运行")
        if args.command == "verify":
            report = verify(args.output)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 1
        if args.command == "build-catalog":
            from .display_catalog import build_catalog

            print(json.dumps(build_catalog(args.output), ensure_ascii=False, indent=2))
            return 0
        client = Client(args.timeout, args.retries, args.delay, args.max_pdf_mb)
        collector = Collector(args.output, client, workers=args.workers, refresh=args.refresh)
        if args.command == "reindex":
            collector.reindex()
        if args.command in {"collect", "discover"}:
            collector.discover_pages(extra_seeds=args.seed, max_pages=args.max_pages)
        if args.command in {"collect", "download"}:
            collector.download(scopes=args.scope, max_documents=args.max_documents)
        report = collector.export()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        # Source HTTP failures remain explicit in the report; unfinished work has a separate code.
        return (
            2
            if report["pending_pages"]
            or (
                args.command in {"collect", "download"}
                and not args.scope
                and report["pending_documents"]
            )
            else 0
        )
