import argparse
import fcntl
import json
from pathlib import Path

from .collector import build, collect, make_archive


def main():
    parser = argparse.ArgumentParser(description="采集 Skydio 官方支持文档、手册与型号参数")
    parser.add_argument(
        "command", choices=["collect", "discover", "download", "build-catalog", "verify", "report"]
    )
    parser.add_argument("--output", type=Path, default=Path("data/Skydio无人机文档"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be between 1 and 8")
    archive = make_archive(args.output, workers=args.workers, refresh=args.refresh)
    with (archive.root / ".collector.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command in {"collect", "discover"}:
            collect(archive)
        if args.command in {"collect", "download"}:
            archive.download()
        if args.command in {"collect", "build-catalog"}:
            report = build(archive)
        elif args.command == "verify":
            report = archive.verify()
        else:
            report = archive.export()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report.get("ok", True):
            return 1
        summary = archive.export()
        return (
            2
            if any(summary[k] for k in ["failed_pages", "failed_documents", "pending_documents"])
            else 0
        )
