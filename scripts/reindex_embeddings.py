#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

for import_path in (PROJECT_ROOT, BACKEND_DIR):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from app.core.config import get_settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.embedding_reindex import reindex_embeddings  # noqa: E402
from app.services.embeddings import build_embedding_service  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild Qdrant vectors for existing content chunks.")
    parser.add_argument(
        "--collection",
        default=None,
        help="Target Qdrant collection. Defaults to QDRANT_COLLECTION from settings.",
    )
    parser.add_argument(
        "--reset-collection",
        action="store_true",
        help="Delete and recreate only the target collection before indexing.",
    )
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    settings = get_settings()
    embedding = build_embedding_service(settings)
    collection = args.collection or settings.qdrant_collection
    async with AsyncSessionLocal() as session:
        summary = await reindex_embeddings(
            session,
            qdrant_url=settings.qdrant_url,
            collection=collection,
            embedding=embedding,
            reset_collection=args.reset_collection,
        )
    print(json.dumps(summary.to_json_dict(), sort_keys=True, ensure_ascii=False))
    return 1 if summary.failed else 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
