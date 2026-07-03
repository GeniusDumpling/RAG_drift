"""Reindex all chunks with the new embedding model into the new Qdrant collection.

Usage:
    cd /home/admin/.openclaw/workspace/RAG_drift
    HF_HUB_OFFLINE=1 .venv/bin/python backend/scripts/reindex_embeddings.py
"""
import asyncio
import os
import sys

# Ensure we can import from the project
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal as async_session_factory
from app.services.embedding_reindex import reindex_embeddings
from app.services.embeddings import build_embedding_service


async def main():
    settings = get_settings()
    embedding = build_embedding_service(settings)
    print(f"Provider: {embedding.__class__.__name__}")
    print(f"Model   : {embedding.model_name}")
    print(f"Dim     : {embedding.dimension}")
    print(f"Qdrant  : {settings.qdrant_url} / {settings.qdrant_collection}")
    print(f"Reset   : True (new collection)")

    async with async_session_factory() as session:
        summary = await reindex_embeddings(
            session,
            qdrant_url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            embedding=embedding,
            reset_collection=True,
        )
    print("\n=== Reindex Summary ===")
    print(f"Total    : {summary.total}")
    print(f"Success  : {summary.succeeded}")
    print(f"Failed   : {summary.failed}")
    print(f"Provider : {summary.provider}")
    print(f"Model    : {summary.model}")
    print(f"Dim      : {summary.dimension}")
    print(f"Collection: {summary.collection}")
    print(f"Backend  : {summary.backend_name}")


if __name__ == "__main__":
    asyncio.run(main())
