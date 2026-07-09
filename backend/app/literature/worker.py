from __future__ import annotations

import asyncio
import uuid

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.literature.pipeline import LiteraturePipeline
from app.repositories.literature import LiteratureRepository


async def process_one(run_id: uuid.UUID | None = None) -> str | None:
    async with AsyncSessionLocal() as session:
        repo = LiteratureRepository(session)
        run = await repo.claim_next_run(run_id)
        if run is None:
            await session.rollback()
            return None
        claimed_id = run.id
        await session.commit()
        completed = await LiteraturePipeline(session).execute(claimed_id)
        return completed.status


async def run_forever() -> None:
    interval = max(0.2, float(get_settings().worker_poll_interval_seconds))
    while True:
        status = await process_one()
        if status is None:
            await asyncio.sleep(interval)
