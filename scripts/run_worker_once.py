#!/usr/bin/env python3
import argparse
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

for import_path in (PROJECT_ROOT, BACKEND_DIR):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from worker.app.runner import run_once  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claim and process one queued crawl run.")
    parser.add_argument(
        "--run-id",
        type=uuid.UUID,
        default=None,
        help="Only claim and process this queued crawl run UUID.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = run_once(run_limit=1, run_id=args.run_id)
    print(result)
