#!/usr/bin/env python3
"""Run the IEEE literature research worker."""

import argparse
import asyncio
import json
import os
import sys
import uuid

# Ensure backend/ is on the Python path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_PROJECT_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.literature.worker import process_one, run_forever  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the IEEE literature research worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--run-id", type=uuid.UUID, help="Process one specific queued run")
    parser.add_argument("--json", action="store_true", help="Print the one-shot result as JSON")
    args = parser.parse_args()

    # Keep the former explicit-run behaviour available for existing schedulers.
    legacy_run_id = os.environ.get("LITERATURE_RUN_ID")
    run_id = args.run_id or (uuid.UUID(legacy_run_id) if legacy_run_id else None)
    if args.once or run_id is not None:
        status = asyncio.run(process_one(run_id))
        if args.json:
            print(json.dumps({"run_id": str(run_id) if run_id else None, "status": status}))
        elif status is None:
            print("No queued literature run found.")
        else:
            print(f"Literature run finished with status={status}")
        return

    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
