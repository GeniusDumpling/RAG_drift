#!/usr/bin/env python3
import argparse
import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

for import_path in (PROJECT_ROOT, BACKEND_DIR):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from worker.app.runner import WorkerRunResult, run_once  # noqa: E402

SUCCESS_PAYLOAD = {"claimed": 1, "succeeded": 1, "partial": 0, "failed": 0}


def result_payload(result: WorkerRunResult) -> dict[str, int]:
    return {
        "claimed": result.claimed,
        "succeeded": result.succeeded,
        "partial": result.partial,
        "failed": result.failed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claim and process one queued crawl run.")
    parser.add_argument(
        "--run-id",
        type=uuid.UUID,
        default=None,
        help="Only claim and process this queued crawl run UUID.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable worker result JSON object.",
    )
    parser.add_argument(
        "--require-success",
        action="store_true",
        help=(
            "Exit nonzero unless exactly one run was claimed, succeeded, and had no "
            "partial or failed outcomes."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_once(run_limit=1, run_id=args.run_id)
    payload = result_payload(result)
    if args.json:
        print(json.dumps(payload, separators=(",", ":")))
    else:
        print(result)

    if args.require_success and payload != SUCCESS_PAYLOAD:
        print(
            "Worker did not complete exactly one run successfully: "
            f"{json.dumps(payload, sort_keys=True)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
