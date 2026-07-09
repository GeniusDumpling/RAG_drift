#!/usr/bin/env python3
"""One-shot literature worker that claims and processes a single queued run."""

import os
import sys

# Ensure backend/ is on the Python path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_PROJECT_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import asyncio
import uuid

from app.literature.worker import process_one, run_forever


def main() -> None:
    run_id_str = os.environ.get("LITERATURE_RUN_ID")
    run_id: uuid.UUID | None = uuid.UUID(run_id_str) if run_id_str else None
    status = asyncio.run(process_one(run_id))
    if status is None:
        print("No queued literature run found.")
    else:
        print(f"Literature run completed with status: {status}")


if __name__ == "__main__":
    main()
