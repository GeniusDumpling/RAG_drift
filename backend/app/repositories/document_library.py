"""Read published and in-progress document artifacts without running the ingestion code."""

from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories.database_catalog import checked_execute


def search_pattern(query: str) -> str:
    return "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def models(session: AsyncSession) -> dict:
    result = await checked_execute(
        session,
        """
        SELECT m.id,m.slug,m.name,m.manufacturer,count(l.document_version_id) AS documents
        FROM drone_models m LEFT JOIN document_model_links l ON l.model_id=m.id
        GROUP BY m.id ORDER BY m.manufacturer,m.name
    """,
    )
    return {"items": [dict(r) for r in result.mappings()]}


async def list_documents(
    session: AsyncSession, *, q: str | None, model: str | None, limit: int, offset: int
) -> dict:
    conditions, params = [], {"limit": limit, "offset": offset}
    if q and q.strip():
        conditions.append("""(d.title ILIKE :q ESCAPE '\\' OR EXISTS (
            SELECT 1 FROM document_model_links l JOIN drone_models m ON m.id=l.model_id
            WHERE l.document_version_id=d.id AND (m.name ILIKE :q ESCAPE '\\'
            OR m.manufacturer ILIKE :q ESCAPE '\\')))""")
        params["q"] = search_pattern(q)
    if model:
        conditions.append("""EXISTS (SELECT 1 FROM document_model_links l
            JOIN drone_models m ON m.id=l.model_id
            WHERE l.document_version_id=d.id AND m.slug=:model)""")
        params["model"] = model
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    total = (
        await checked_execute(session, "SELECT count(*) FROM document_versions d" + where, params)
    ).scalar_one()
    result = await checked_execute(
        session,
        """
        SELECT d.id,d.title,d.document_type,d.official_version,d.published_at,
            d.active_processing_id,p.id AS processing_id,p.status,p.created_at,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('slug',m.slug,'name',m.name,
                'manufacturer',m.manufacturer) ORDER BY m.name)
                FROM document_model_links l JOIN drone_models m ON m.id=l.model_id
                WHERE l.document_version_id=d.id),'[]'::jsonb) AS models
        FROM document_versions d LEFT JOIN LATERAL (
            SELECT id,status,created_at FROM document_processings
            WHERE document_version_id=d.id
            ORDER BY (id=d.active_processing_id) DESC NULLS LAST,created_at DESC,id LIMIT 1
        ) p ON true
    """
        + where
        + " ORDER BY p.created_at DESC NULLS LAST,d.id LIMIT :limit OFFSET :offset",
        params,
    )
    return {
        "items": [dict(r) for r in result.mappings()],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def select_processing(
    session: AsyncSession, document_id: UUID, processing_id: UUID | None = None
) -> dict:
    result = await checked_execute(
        session,
        """
        SELECT p.*,(p.id=d.active_processing_id) AS is_active FROM document_processings p
        JOIN document_versions d ON d.id=p.document_version_id
        WHERE d.id=:document AND (CAST(:processing AS uuid) IS NULL
            OR p.id=CAST(:processing AS uuid))
        ORDER BY (p.id=d.active_processing_id) DESC NULLS LAST,p.created_at DESC,p.id LIMIT 1
    """,
        {"document": document_id, "processing": str(processing_id) if processing_id else None},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(404, "Document processing not found")
    return dict(row)


async def detail(
    session: AsyncSession, document_id: UUID, processing_id: UUID | None = None
) -> dict:
    selected = await select_processing(session, document_id, processing_id)
    # Filesystem paths are internal deployment details, not document content.
    selected.pop("artifact_root", None)
    document = (
        (
            await checked_execute(
                session, "SELECT * FROM document_versions WHERE id=:id", {"id": document_id}
            )
        )
        .mappings()
        .one()
    )
    versions = (
        await checked_execute(
            session,
            """SELECT id,status,parser_version,chunker_version,
        language_policy,created_at,indexed_at FROM document_processings
        WHERE document_version_id=:id ORDER BY created_at DESC,id""",
            {"id": document_id},
        )
    ).mappings()
    linked_models = (
        await checked_execute(
            session,
            """SELECT m.slug,m.name,m.manufacturer,
        l.applicability_note FROM document_model_links l JOIN drone_models m ON m.id=l.model_id
        WHERE l.document_version_id=:id ORDER BY m.name""",
            {"id": document_id},
        )
    ).mappings()
    params = {"id": selected["id"]}
    stats = (
        (
            await checked_execute(
                session,
                """SELECT
        (SELECT count(*) FROM document_pages WHERE processing_id=:id) AS pages,
        (SELECT count(*) FROM document_pages WHERE processing_id=:id AND included) AS chinese_pages,
        (SELECT count(*) FROM document_elements WHERE processing_id=:id) AS elements,
        (SELECT count(*) FROM content_chunks WHERE processing_id=:id) AS chunks,
        (SELECT count(*) FROM content_chunks WHERE processing_id=:id
            AND embed_status='success') AS indexed_chunks,
        (SELECT count(*) FROM document_elements WHERE processing_id=:id
            AND element_type='table') AS tables,
        (SELECT count(*) FROM document_elements WHERE processing_id=:id
            AND element_type='figure') AS figures,
        (SELECT count(*) FROM document_elements WHERE processing_id=:id
            AND metadata_json->'quality_gate'->>'status'='quarantined') AS quarantined_figures
    """,
                params,
            )
        )
        .mappings()
        .one()
    )
    return {
        "document": dict(document),
        "models": [dict(m) for m in linked_models],
        "processing": selected,
        "processings": [dict(p) for p in versions],
        "stats": dict(stats),
    }


async def section(
    session: AsyncSession,
    document_id: UUID,
    name: str,
    *,
    processing_id: UUID | None,
    kind: str | None,
    page_no: int | None,
    limit: int,
    offset: int,
) -> dict:
    processing = await select_processing(session, document_id, processing_id)
    config = {
        "pages": ("document_pages", "source_page_no,id"),
        "elements": ("document_elements", "reading_order,id"),
        "chunks": ("content_chunks", "chunk_index,id"),
    }
    if name not in config:
        raise HTTPException(404, "Document section not found")
    table, order = config[name]
    params = {"id": processing["id"], "limit": limit, "offset": offset}
    where = " WHERE t.processing_id=:id"
    if name == "elements" and kind:
        where += " AND t.element_type=:kind"
        params["kind"] = kind
    if page_no is not None:
        params["page"] = page_no
        if name == "pages":
            where += " AND t.source_page_no=:page"
        elif name == "elements":
            where += """ AND EXISTS (SELECT 1 FROM element_regions r
                JOIN document_pages p ON p.id=r.page_id
                WHERE r.element_id=t.id AND p.source_page_no=:page)"""
        else:
            where += """ AND EXISTS (SELECT 1 FROM chunk_elements ce
                JOIN element_regions r ON r.element_id=ce.element_id
                JOIN document_pages p ON p.id=r.page_id
                WHERE ce.chunk_id=t.id AND p.source_page_no=:page)"""
    total = (
        await checked_execute(session, f"SELECT count(*) FROM {table} t{where}", params)
    ).scalar_one()
    extra = ""
    if name == "elements":
        extra = """, COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'page_id',p.id,'source_page_no',p.source_page_no,'bbox',r.bbox)
            ORDER BY p.source_page_no,r.region_index) FROM element_regions r
            JOIN document_pages p ON p.id=r.page_id
            WHERE r.element_id=t.id),'[]'::jsonb) AS regions"""
    result = await checked_execute(
        session,
        f"SELECT t.*{extra} FROM {table} t{where} ORDER BY {order} LIMIT :limit OFFSET :offset",
        params,
    )
    return {
        "items": [dict(r) for r in result.mappings()],
        "total": total,
        "limit": limit,
        "offset": offset,
        "processing_id": processing["id"],
    }


def resolve_asset(storage_key: str) -> Path:
    settings = get_settings()
    root = settings.document_artifacts_dir.resolve()
    stored = Path(storage_key)
    if stored.is_absolute() and settings.document_artifacts_source_root:
        try:
            relative = stored.relative_to(settings.document_artifacts_source_root)
        except ValueError as exc:
            raise HTTPException(404, "Document asset unavailable") from exc
        candidate = root / relative
    else:
        candidate = stored if stored.is_absolute() else root / stored
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise HTTPException(404, "Document asset unavailable")
    return candidate


async def asset(session: AsyncSession, asset_id: UUID) -> dict:
    row = (
        (
            await checked_execute(
                session,
                "SELECT storage_key,mime_type FROM file_assets WHERE id=:id",
                {"id": asset_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["mime_type"] not in {
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
    }:
        raise HTTPException(404, "Document asset unavailable")
    return {"path": resolve_asset(row["storage_key"]), "media_type": row["mime_type"]}
