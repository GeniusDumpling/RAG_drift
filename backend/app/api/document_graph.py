"""Read-only multimodal document graph APIs; images are served from validated assets."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.services.document_graph import queries
from app.services.document_graph.storage import DocumentGraphStore

router = APIRouter(prefix="/document-graph", tags=["document-graph"])
Digest = Annotated[str, Query(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")]


async def _query(callback):
    def run():
        try:
            with DocumentGraphStore(get_settings()) as store:
                return callback(store)
        except HTTPException:
            raise
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="资源不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="文档图谱或图文资源不可用") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Neo4j 文档图谱暂不可用") from exc

    return await run_in_threadpool(run)


@router.get("/retrieve")
async def retrieve(
    q: str = Query(default="", max_length=1000),
    source_id: Digest | None = None,
    limit: int = Query(default=10, ge=1, le=100),
):
    return await _query(lambda store: queries.retrieve(store, q, source_id=source_id, limit=limit))


@router.get("/status")
async def status(source_id: Digest):
    def inspect(store):
        row = store.status(source_id)
        if not row:
            raise HTTPException(status_code=404, detail="找不到文档图谱")
        return dict(
            source_id=source_id,
            title=row["s"].get("title"),
            model=row["s"].get("model"),
            withdrawn=bool(row["s"].get("withdrawn")),
            active_build_id=row["s"].get("active_build_id"),
            desired_status=row["d"].get("status") if row.get("d") else None,
            verification=queries.verify(store, source_id),
        )

    return await _query(inspect)


@router.get("/assets/{source_id}/{asset_id}")
async def image_asset(source_id: str, asset_id: str):
    import re

    if not all(re.fullmatch("[a-f0-9]{64}", v) for v in [source_id, asset_id]):
        raise HTTPException(status_code=404, detail="资源不存在")
    path, record = await _query(lambda store: queries.asset(store, source_id, asset_id))
    return FileResponse(
        path, media_type=record["mime_type"], headers={"ETag": f'"{record["sha256"]}"'}
    )
