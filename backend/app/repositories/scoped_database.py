"""Fixed table projections for the independent literature and document databases."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories.database import fetch_qdrant_collection_status
from app.repositories.database_catalog import checked_execute, get_target
from app.schemas.database import (
    DatabaseOverview,
    DatabaseTableMeta,
    DatabaseTableRow,
    DatabaseTableRowsPage,
)


@dataclass(frozen=True)
class Projection:
    label: str
    columns: str
    order: str
    search: str = ""
    keys: str = "id"


DOCUMENT_TABLES = {
    "document_versions": Projection(
        "文档版本",
        "id,title,document_type,official_version,published_at,active_processing_id,source_url,metadata_json",
        "title",
        "title,document_type,official_version",
    ),
    "drone_models": Projection(
        "无人机型号", "id,slug,name,manufacturer", "name", "slug,name,manufacturer"
    ),
    "document_processings": Projection(
        "处理版本",
        "id,document_version_id,status,parser_version,chunker_version,created_at,indexed_at,metadata_json",
        "created_at DESC",
        "status,parser_version",
    ),
    "document_pages": Projection(
        "页面与语言筛选",
        "id,processing_id,source_page_no,printed_page_label,language_state,included,inclusion_reason,render_asset_id,metadata_json",
        "processing_id,source_page_no",
        "language_state,inclusion_reason",
    ),
    "document_elements": Projection(
        "正文、表格与图像元素",
        "id,processing_id,element_type,reading_order,section_path,language,text,table_json,asset_id,metadata_json",
        "processing_id,reading_order",
        "element_type,text,section_path",
    ),
    "content_chunks": Projection(
        "检索切片",
        "id,processing_id,chunk_index,chunk_type,section_path,display_text,embed_status,index_language,metadata_json",
        "processing_id,chunk_index",
        "display_text,chunk_type,section_path",
    ),
    "file_assets": Projection(
        "文件与图片", "id,sha256,mime_type,bytes,width,height", "id", "mime_type,sha256"
    ),
    "document_model_links": Projection(
        "文档适用型号",
        "document_version_id,model_id,applicability_note",
        "document_version_id,model_id",
        "applicability_note",
        "document_version_id,model_id",
    ),
    "element_regions": Projection(
        "原文位置",
        "element_id,page_id,region_index,bbox",
        "page_id,region_index",
        "",
        "element_id,page_id,region_index",
    ),
    "element_links": Projection(
        "图文及脚注关联",
        "from_element_id,to_element_id,relation_type",
        "from_element_id,to_element_id",
        "relation_type",
        "from_element_id,to_element_id,relation_type",
    ),
    "chunk_elements": Projection(
        "切片证据关联",
        "chunk_id,element_id,sequence_no",
        "chunk_id,sequence_no",
        "",
        "chunk_id,element_id",
    ),
    "vector_index_jobs": Projection(
        "向量索引任务",
        "chunk_id,collection,model_version,status,attempts,last_error,updated_at",
        "updated_at DESC",
        "status,collection,last_error",
        "chunk_id,collection,model_version",
    ),
}
LITERATURE_TABLES = {
    "literature_runs": Projection(
        "文献研究任务",
        "id,query,status,current_stage,progress_current,progress_total,progress_message,created_at,error_message",
        "created_at DESC",
        "query,status",
    ),
    "literature_papers": Projection(
        "文献",
        "id,title,doi,publication_title,publication_year,abstract,access_type,document_url,created_at",
        "created_at DESC",
        "title,doi,abstract",
    ),
    "literature_run_events": Projection(
        "文献任务事件",
        "id,literature_run_id,stage,level,event_type,message,created_at",
        "created_at DESC",
        "message,stage,event_type",
    ),
    "literature_run_papers": Projection(
        "任务与文献分析",
        "id,literature_run_id,paper_id,direction_title,selected,analysis_status,abstract_zh,conclusion",
        "created_at DESC",
        "direction_title,abstract_zh,conclusion",
    ),
    "literature_artifacts": Projection(
        "文献处理产物",
        "id,literature_run_id,paper_id,artifact_type,mime_type,byte_size,source_url,created_at",
        "created_at DESC",
        "artifact_type,source_url",
    ),
    "literature_evidence": Projection(
        "文献证据",
        "id,literature_run_paper_id,matched_term,evidence_text,page_number,verified,created_at",
        "created_at DESC",
        "matched_term,evidence_text",
    ),
}


def projections(database: str) -> dict[str, Projection]:
    get_target(database)
    return DOCUMENT_TABLES if database == "documents" else LITERATURE_TABLES


def metadata(database: str) -> list[DatabaseTableMeta]:
    return [
        DatabaseTableMeta(
            table_name=name,
            label=f"{p.label} {name}",
            description=p.label,
            default_sort=p.order,
            preview_columns=p.columns.split(","),
            searchable_columns=p.search.split(",") if p.search else [],
        )
        for name, p in projections(database).items()
    ]


def value(v):
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, date | datetime):
        return v.isoformat()
    if isinstance(v, str):
        return v[:240]
    return v


async def rows(
    session: AsyncSession,
    database: str,
    table: str,
    limit: int,
    offset: int,
    q: str | None = None,
    row_id: UUID | None = None,
) -> DatabaseTableRowsPage:
    p = projections(database).get(table)
    if p is None or (row_id is not None and p.keys != "id"):
        raise HTTPException(404, "Database table not found")
    where, params = "", {"limit": limit, "offset": offset}
    if row_id is not None:
        where = " WHERE id=:id"
        params["id"] = row_id
    elif q and q.strip() and p.search:
        where = (
            " WHERE ("
            + " OR ".join(f"CAST({c} AS text) ILIKE :q ESCAPE '\\'" for c in p.search.split(","))
            + ")"
        )
        params["q"] = (
            "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
    total = (
        await checked_execute(session, f"SELECT count(*) FROM {table}{where}", params)
    ).scalar_one()
    result = await checked_execute(
        session,
        f"SELECT {p.columns} FROM {table}{where} ORDER BY {p.order} LIMIT :limit OFFSET :offset",
        params,
    )
    items = [
        DatabaseTableRow(
            id="/".join(str(r[k]) for k in p.keys.split(",")),
            table_name=table,
            preview={k: value(v) for k, v in r.items()},
        )
        for r in result.mappings()
    ]
    return DatabaseTableRowsPage(items=items, total=total, limit=limit, offset=offset)


async def overview(session: AsyncSession, database: str) -> DatabaseOverview:
    identity = (
        (
            await checked_execute(
                session,
                "SELECT current_database() AS name, version() AS version, now() AS checked_at",
            )
        )
        .mappings()
        .one()
    )
    counts = [
        {
            "table_name": table,
            "count": (await checked_execute(session, f"SELECT count(*) FROM {table}")).scalar_one(),
        }
        for table in projections(database)
    ]
    statuses = []
    if database == "documents":
        result = await checked_execute(
            session,
            "SELECT embed_status, count(*) AS count FROM content_chunks GROUP BY embed_status",
        )
        statuses = [{**dict(r), "vector_backend": "qdrant"} for r in result.mappings()]
        s = get_settings()
        qdrant = await fetch_qdrant_collection_status(
            s.document_qdrant_url or s.qdrant_url, get_target(database).collection
        )
        qdata = qdrant.__dict__
        vector_count = sum(r["count"] for r in statuses if r["embed_status"] == "success")
        match = (
            "unknown"
            if qdrant.points_count is None
            else "matched"
            if vector_count == qdrant.points_count
            else "mismatch"
        )
    else:
        qdata = {
            "status": "not_applicable",
            "collection": "无向量集合",
            "points_count": None,
            "vector_size": None,
            "distance": None,
        }
        vector_count, match = 0, "not_applicable"
    return DatabaseOverview.model_validate(
        {
            "postgres": {
                "database_name": identity["name"],
                "server_version": identity["version"],
                "checked_at": identity["checked_at"],
                "table_counts": counts,
                "latest_run": None,
                "latest_search": None,
                "chunk_vector_status": statuses,
            },
            "qdrant": qdata,
            "reconciliation": {
                "postgres_qdrant_chunk_count": vector_count,
                "qdrant_points_count": qdata["points_count"],
                "status": match,
            },
        }
    )
