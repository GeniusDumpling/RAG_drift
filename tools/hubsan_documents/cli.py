import argparse
import fcntl
import json
from pathlib import Path

from tools.autel_documents.collector import write_json

from .collector import build, collect, make_archive


def main():
    parser = argparse.ArgumentParser(description="采集 Hubsan 官方手册、ZIP 手册和型号参数")
    parser.add_argument(
        "command", choices=["collect", "discover", "download", "build-catalog", "verify", "report"]
    )
    parser.add_argument("--output", type=Path, default=Path("data/Hubsan无人机文档"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be between 1 and 8")
    archive = make_archive(args.output, workers=args.workers, refresh=args.refresh)
    image_report = None
    with (archive.root / ".collector.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command in {"collect", "discover"}:
            collect(archive)
        if args.command in {"collect", "download"}:
            archive.download()
        if args.command == "collect":
            from .images import collect_images

            image_report = collect_images(archive.root, refresh=args.refresh)
        if args.command in {"collect", "build-catalog"}:
            report = build(archive)
        elif args.command == "verify":
            report = archive.verify()
            if (archive.root / "参数图片/采集状态.json").exists():
                report["images"] = make_archive(archive.root / "参数图片").verify()
                report["ok"] = report["ok"] and report["images"]["ok"]
                write_json(archive.root / "校验报告.json", report)
        else:
            report = archive.export()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report.get("ok", True):
            return 1
        summary = archive.export()
        if image_report and image_report["failed_pages"]:
            return 2
        return (
            2
            if any(summary[k] for k in ["failed_pages", "failed_documents", "pending_documents"])
            else 0
        )
