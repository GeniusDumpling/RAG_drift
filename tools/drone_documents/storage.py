import asyncio
import os
import re
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import psycopg
from app.core.config import get_settings
from dotenv import dotenv_values
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from qdrant_client import QdrantClient, models
from sqlalchemy.engine import make_url

from .common import (
    ROOT,
    progress,
    read_json,
    read_jsonl,
    uid,
    validate_bundle,
    write_json,
)

DEFAULT_COLLECTION = "drone_document_chunks_bge_m3_v1"


def configuration(database_url=None, qdrant_url=None, collection=None):
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    settings = get_settings()
    original = make_url(settings.sync_database_url)
    target = make_url(
        database_url or env.get("DOCUMENT_DATABASE_URL") or original.set(database="drone_documents")
    )
    if target.database in {original.database, "postgres", "template0", "template1", None}:
        raise ValueError("文档工具只允许使用独立文档数据库，不能使用业务库或系统库。")
    target_collection = collection or env.get("DOCUMENT_QDRANT_COLLECTION") or DEFAULT_COLLECTION
    if target_collection == settings.qdrant_collection:
        raise ValueError("文档 collection 必须与现有业务 collection 分开。")
    return (
        target.set(drivername="postgresql").render_as_string(hide_password=False),
        qdrant_url or env.get("DOCUMENT_QDRANT_URL") or "http://127.0.0.1:6333",
        target_collection,
    )


def bootstrap(database_url=None) -> dict:
    dsn, _, _ = configuration(database_url)
    target = make_url(dsn)
    admin = target.set(database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True, connect_timeout=10) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (target.database,)
        ).fetchone()
        if not exists:
            conn.execute(
                sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(
                    sql.Identifier(target.database)
                )
            )
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.execute(Path(__file__).with_name("schema.sql").read_text())
    result = {
        "database": target.database,
        "created": not bool(exists),
        "schema": "0001_drone_documents",
    }
    progress("database_initialized", **result)
    return result


def upsert(conn, table: str, row: dict, *, conflict="id", preserve=()):
    keys = list(row)
    updates = [k for k in keys if k not in {conflict, *preserve}]
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) ").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, keys)),
        sql.SQL(",").join(sql.Placeholder() for _ in keys),
        sql.Identifier(conflict),
    )
    if updates:
        query += sql.SQL("DO UPDATE SET ") + sql.SQL(",").join(
            sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(k), sql.Identifier(k)) for k in updates
        )
    else:
        query += sql.SQL("DO NOTHING")
    values = [Jsonb(v) if isinstance(v, dict | list) else v for v in row.values()]
    conn.execute(query, values)


def import_bundle(root: Path, *, database_url=None, collection=None) -> dict:
    manifest = validate_bundle(root)
    if manifest["stage"] != "enriched":
        raise ValueError("必须先完成全部图片描述；有失败时重跑 enrich，再导入。")
    dsn, _, collection = configuration(database_url, collection=collection)
    docs = read_jsonl(root / "documents.jsonl")
    assets = read_jsonl(root / "assets.jsonl")
    elements = read_jsonl(root / "elements.jsonl")
    chunks = read_jsonl(root / "chunks.jsonl")
    settings = get_settings()
    artifact_root = str(root.resolve())
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        # A transaction lock protects import/upsert of a bundle from concurrent imports.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("drone-documents:" + manifest["model_slug"],),
        )
        for row in read_jsonl(root / "models.jsonl"):
            upsert(conn, "drone_models", row)
        for row in assets:
            upsert(
                conn,
                "file_assets",
                {**row, "storage_key": str((root / row["storage_key"]).resolve())},
            )
        for doc in docs:
            upsert(
                conn,
                "document_versions",
                {
                    **{
                        k: doc[k]
                        for k in [
                            "id",
                            "document_key",
                            "title",
                            "document_type",
                            "official_version",
                            "published_at",
                            "source_url",
                            "source_asset_id",
                        ]
                    },
                    "metadata_json": {**doc["metadata_json"], "scope_note": doc["scope_note"]},
                },
            )
            previous = conn.execute(
                "SELECT status FROM document_processings WHERE id=%s", (doc["processing_id"],)
            ).fetchone()
            # Publishing a processing makes its evidence immutable. A changed parser,
            # prompt/model or chunker needs a new processing identity.
            if previous and previous["status"] == "ready":
                old = {
                    str(r["id"]): r["content_hash"]
                    for r in conn.execute(
                        "SELECT id,content_hash FROM content_chunks WHERE processing_id=%s",
                        (doc["processing_id"],),
                    )
                }
                new = {
                    c["id"]: c["content_hash"]
                    for c in chunks
                    if c["processing_id"] == doc["processing_id"]
                }
                if old != new:
                    raise ValueError("已发布解析产物不可覆盖，请使用新处理配置重新 prepare。")
            upsert(
                conn,
                "document_processings",
                {
                    "id": doc["processing_id"],
                    "document_version_id": doc["id"],
                    "parser_version": doc["parser_version"],
                    "config_hash": doc["config_hash"],
                    "language_policy": manifest["config"]["language_policy"],
                    "chunker_version": manifest["pipeline_version"],
                    "status": previous["status"] if previous else "pending_index",
                    "artifact_root": artifact_root,
                    "metadata_json": {
                        "model_slugs": doc["model_slugs"],
                        "scope_note": doc["scope_note"],
                        "vlm_model": manifest["vlm_model"],
                        "vlm_prompt_version": manifest["vlm_prompt_version"],
                    },
                },
            )
            for slug in doc["model_slugs"]:
                conn.execute(
                    """INSERT INTO document_model_links VALUES (%s,%s,%s)
                    ON CONFLICT(document_version_id,model_id) DO UPDATE
                    SET applicability_note=EXCLUDED.applicability_note""",
                    (doc["id"], uid("model", slug), doc["scope_note"]),
                )
        for page in read_jsonl(root / "pages.jsonl"):
            upsert(conn, "document_pages", page)
        for element in elements:
            upsert(
                conn,
                "document_elements",
                {k: v for k, v in element.items() if k not in {"document_version_id", "regions"}},
            )
            for index, region in enumerate(element["regions"]):
                conn.execute(
                    """INSERT INTO element_regions VALUES(%s,%s,%s,%s)
                    ON CONFLICT(element_id,page_id,region_index)
                    DO UPDATE SET bbox=EXCLUDED.bbox""",
                    (element["id"], region["page_id"], index, Jsonb(region["bbox"])),
                )
        for link in read_jsonl(root / "links.jsonl"):
            conn.execute(
                "INSERT INTO element_links VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (link["from_element_id"], link["to_element_id"], link["relation_type"]),
            )
        for chunk in chunks:
            upsert(
                conn,
                "content_chunks",
                {k: v for k, v in chunk.items() if k not in {"document_version_id", "element_ids"}},
            )
            for index, element_id in enumerate(chunk["element_ids"]):
                conn.execute(
                    """INSERT INTO chunk_elements VALUES (%s,%s,%s)
                    ON CONFLICT(chunk_id,element_id)
                    DO UPDATE SET sequence_no=EXCLUDED.sequence_no""",
                    (chunk["id"], element_id, index),
                )
            conn.execute(
                """INSERT INTO vector_index_jobs
                (chunk_id,collection,model_version,input_hash,status) VALUES(%s,%s,%s,%s,'pending')
                ON CONFLICT(chunk_id,collection,model_version) DO UPDATE
                SET status=CASE WHEN vector_index_jobs.input_hash=EXCLUDED.input_hash
                                THEN vector_index_jobs.status ELSE 'pending' END,
                    input_hash=EXCLUDED.input_hash,updated_at=now()""",
                (chunk["id"], collection, settings.embedding_model, chunk["content_hash"]),
            )
    result = {
        "database": make_url(dsn).database,
        "documents": len(docs),
        "elements": len(elements),
        "chunks": len(chunks),
        "collection": collection,
    }
    write_json(root / "import-report.json", result)
    progress("imported", **result)
    return result


def ensure_collection(client: QdrantClient, collection: str):
    if not client.collection_exists(collection):
        client.create_collection(
            collection,
            vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
        )
    info = client.get_collection(collection)
    vectors = info.config.params.vectors
    if not isinstance(vectors, models.VectorParams) or vectors.size != 1024:
        raise ValueError("现有文档 collection 的向量配置不是 BGE-M3 1024 维。")
    if vectors.distance != models.Distance.COSINE:
        raise ValueError("现有文档 collection 距离配置不是 Cosine。")
    for field in [
        "corpus",
        "model_slugs",
        "document_type",
        "processing_id",
        "index_language",
        "chunk_type",
        "software_context",
    ]:
        if field not in info.payload_schema:
            client.create_payload_index(
                collection, field, models.PayloadSchemaType.KEYWORD, wait=True
            )


async def _embed_texts(texts: list[str], settings) -> list[list[float]]:
    key = settings.embedding_api_key or settings.vlm_api_key
    if not key:
        raise ValueError("缺少嵌入模型密钥")
    if settings.embedding_model != "BAAI/bge-m3":
        raise ValueError("该 collection 只接受 BAAI/bge-m3，请为其他模型新建 collection。")
    async with httpx.AsyncClient(timeout=90) as client:
        for attempt in range(3):
            try:
                response = await client.post(
                    (settings.embedding_base_url or settings.vlm_base_url).rstrip("/")
                    + "/embeddings",
                    headers={"Authorization": "Bearer " + key},
                    json={"model": settings.embedding_model, "input": texts},
                )
                response.raise_for_status()
                vectors = [
                    r["embedding"]
                    for r in sorted(response.json()["data"], key=lambda r: r["index"])
                ]
                if len(vectors) != len(texts) or any(len(v) != 1024 for v in vectors):
                    raise ValueError("嵌入返回数量或维度错误")
                return vectors
            except (httpx.HTTPError, ValueError, KeyError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError("Embedding failed")


async def index_bundle(root: Path, *, database_url=None, qdrant_url=None, collection=None) -> dict:
    validate_bundle(root)
    dsn, qurl, collection = configuration(database_url, qdrant_url, collection)
    settings = get_settings()
    client = QdrantClient(url=qurl, timeout=30)
    ensure_collection(client, collection)
    docs = {d["processing_id"]: d for d in read_jsonl(root / "documents.jsonl")}
    successes, failures, skipped = 0, [], 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s,0))", ("drone-index:" + collection,)
        )
        conn.commit()
        try:
            for pid, doc in docs.items():
                rows = conn.execute(
                    """SELECT c.*,j.status AS job_status,j.input_hash
                    FROM content_chunks c JOIN vector_index_jobs j ON j.chunk_id=c.id
                    WHERE c.processing_id=%s AND j.collection=%s AND j.model_version=%s
                    ORDER BY c.chunk_index""",
                    (pid, collection, settings.embedding_model),
                ).fetchall()
                if not rows:
                    raise ValueError("数据包尚未导入 PostgreSQL。")
                expected_ids = [r["id"] for r in rows]
                pending = []
                # Verify even succeeded jobs against Qdrant so a lost collection
                # can be rebuilt by rerunning index without altering PostgreSQL.
                existing = {
                    str(p.id): p
                    for p in client.retrieve(
                        collection, ids=[str(x) for x in expected_ids], with_payload=True
                    )
                }
                for row in rows:
                    point = existing.get(str(row["id"]))
                    if (
                        row["job_status"] == "succeeded"
                        and point
                        and point.payload.get("content_hash") == row["content_hash"]
                    ):
                        skipped += 1
                        software = row["metadata_json"].get("software_context")
                        if software and point.payload.get("software_context") != software:
                            client.set_payload(
                                collection,
                                payload={"software_context": software},
                                points=[str(row["id"])],
                                wait=True,
                            )
                    else:
                        pending.append(row)
                for start in range(0, len(pending), 16):
                    batch = pending[start : start + 16]
                    try:
                        vectors = []
                        missing, missing_positions = [], []
                        for i, row in enumerate(batch):
                            cache = root / "embedding-cache" / (row["content_hash"] + ".json")
                            if cache.exists():
                                vector = read_json(cache)["vector"]
                                if len(vector) != 1024:
                                    raise ValueError("缓存向量维度错误")
                                vectors.append(vector)
                            else:
                                vectors.append(None)
                                missing.append(row["embed_text"])
                                missing_positions.append(i)
                        if missing:
                            generated = await _embed_texts(missing, settings)
                            for i, vector in zip(missing_positions, generated, strict=False):
                                vectors[i] = vector
                                write_json(
                                    root / "embedding-cache" / (batch[i]["content_hash"] + ".json"),
                                    {"model": settings.embedding_model, "vector": vector},
                                )
                        points = []
                        for row, vector in zip(batch, vectors, strict=False):
                            points.append(
                                models.PointStruct(
                                    id=str(row["id"]),
                                    vector=vector,
                                    payload={
                                        "chunk_id": str(row["id"]),
                                        "corpus": "drone_documents",
                                        "document_version_id": doc["id"],
                                        "processing_id": pid,
                                        "manufacturer": "DJI",
                                        "model_slugs": doc["model_slugs"],
                                        "document_type": doc["document_type"],
                                        "index_language": "zh-Hans",
                                        "chunk_type": row["chunk_type"],
                                        "content_hash": row["content_hash"],
                                        "embedding_model": settings.embedding_model,
                                        "software_context": row["metadata_json"].get(
                                            "software_context"
                                        ),
                                    },
                                )
                            )
                        client.upsert(collection, points=points, wait=True)
                        for row in batch:
                            conn.execute(
                                """UPDATE vector_index_jobs SET status='succeeded',
                                attempts=attempts+1,last_error=NULL,updated_at=now()
                                WHERE chunk_id=%s AND collection=%s AND model_version=%s""",
                                (row["id"], collection, settings.embedding_model),
                            )
                            conn.execute(
                                "UPDATE content_chunks SET embed_status='success' WHERE id=%s",
                                (row["id"],),
                            )
                        conn.commit()
                        successes += len(batch)
                        progress("vector_batch_indexed", document=doc["title"], count=len(batch))
                    except Exception as exc:
                        conn.rollback()
                        error = type(exc).__name__
                        if isinstance(exc, httpx.HTTPStatusError):
                            error += f" HTTP {exc.response.status_code}"
                        for row in batch:
                            conn.execute(
                                """UPDATE vector_index_jobs SET status='failed',
                                attempts=attempts+1,last_error=%s,updated_at=now()
                                WHERE chunk_id=%s AND collection=%s AND model_version=%s""",
                                (error, row["id"], collection, settings.embedding_model),
                            )
                        conn.commit()
                        failures.append(
                            {"document": doc["title"], "batch_start": start, "error": error}
                        )
                        progress("index_batch_failed", **failures[-1])
                remaining = conn.execute(
                    """SELECT count(*) AS n FROM vector_index_jobs j
                    JOIN content_chunks c ON c.id=j.chunk_id WHERE c.processing_id=%s
                    AND j.collection=%s AND j.model_version=%s AND j.status!='succeeded'""",
                    (pid, collection, settings.embedding_model),
                ).fetchone()["n"]
                if remaining == 0:
                    conn.execute(
                        "UPDATE document_processings SET status='ready',indexed_at=now() "
                        "WHERE id=%s",
                        (pid,),
                    )
                    conn.execute(
                        "UPDATE document_versions SET active_processing_id=%s WHERE id=%s",
                        (pid, doc["id"]),
                    )
                    conn.commit()
        finally:
            conn.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s,0))", ("drone-index:" + collection,)
            )
            conn.commit()
    result = {
        "collection": collection,
        "succeeded": successes,
        "skipped": skipped,
        "failures": failures,
        "points_count": client.count(collection, exact=True).count,
    }
    client.close()
    write_json(root / "index-report.json", result)
    progress("indexed", **result)
    return result


async def query(
    root: Path, question: str, *, database_url=None, qdrant_url=None, collection=None, top_k=5
) -> dict:
    dsn, qurl, collection = configuration(database_url, qdrant_url, collection)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        active = [
            str(r["active_processing_id"])
            for r in conn.execute(
                """SELECT DISTINCT
            v.active_processing_id FROM document_versions v JOIN document_model_links l
            ON l.document_version_id=v.id JOIN drone_models m ON m.id=l.model_id
            WHERE m.slug=%s AND v.active_processing_id IS NOT NULL""",
                (read_json(root / "manifest.json")["model_slug"],),
            )
        ]
        if not active:
            raise ValueError("该型号尚无已发布的文档解析产物")
        software = None
        for pattern, name in [
            (r"resolve", "davinci_resolve"),
            (r"premiere", "premiere_pro"),
            (r"final\s*cut", "final_cut_pro"),
        ]:
            if re.search(pattern, question, re.I):
                software = name
                break
        conditions = [
            models.FieldCondition(key="processing_id", match=models.MatchAny(any=active)),
            models.FieldCondition(key="index_language", match=models.MatchValue(value="zh-Hans")),
        ]
        if software:
            conditions.append(
                models.FieldCondition(
                    key="software_context", match=models.MatchValue(value=software)
                )
            )
        vectors = await _embed_texts([question], get_settings())
        with closing(QdrantClient(url=qurl, timeout=20)) as client:
            hits = client.search(
                collection,
                query_vector=vectors[0],
                limit=top_k,
                query_filter=models.Filter(must=conditions),
                with_payload=True,
            )
        # Expand a software-specific workflow with original instruction chunks.
        # Vector similarity alone can retrieve introductory screenshots while
        # missing the native "apply" step on the following page.
        if software:
            found_ids = {str(h.id) for h in hits}
            for term in [
                t for t in ["安装", "应用", "导入", "导出", "调色", "降噪", "锐化"] if t in question
            ]:
                extra = conn.execute(
                    """SELECT id FROM content_chunks WHERE processing_id=ANY(%s::uuid[])
                    AND metadata_json->>'software_context'=%s AND chunk_type IN ('text','table')
                    AND display_text ILIKE %s ORDER BY chunk_index LIMIT 1""",
                    (active, software, "%" + term + "%"),
                ).fetchone()
                if extra and str(extra["id"]) not in found_ids:
                    hits.append(
                        SimpleNamespace(
                            id=extra["id"], score=0.0, retrieval_method="section_keyword_expansion"
                        )
                    )
                    found_ids.add(str(extra["id"]))
        evidence = []
        for hit in hits:
            row = conn.execute(
                """SELECT c.*,v.title,v.source_url,v.document_type FROM content_chunks c
                JOIN document_processings p ON p.id=c.processing_id JOIN document_versions v
                ON v.id=p.document_version_id WHERE c.id=%s AND v.active_processing_id=p.id""",
                (UUID(str(hit.id)),),
            ).fetchone()
            if row:
                linked = conn.execute(
                    """SELECT DISTINCT f.asset_id FROM chunk_elements ce
                    JOIN element_links l ON l.to_element_id=ce.element_id
                    JOIN document_elements f ON f.id=l.from_element_id
                    WHERE ce.chunk_id=%s AND f.element_type='figure' AND f.asset_id IS NOT NULL""",
                    (row["id"],),
                ).fetchall()
                asset_ids = sorted(
                    set(row["metadata_json"].get("asset_ids", []))
                    | {str(r["asset_id"]) for r in linked}
                )
                asset_rows = (
                    conn.execute(
                        "SELECT id,storage_key,mime_type,width,height FROM file_assets "
                        "WHERE id=ANY(%s::uuid[])",
                        (asset_ids,),
                    ).fetchall()
                    if asset_ids
                    else []
                )
                evidence.append(
                    {
                        "chunk_id": str(row["id"]),
                        "score": hit.score,
                        "retrieval_method": getattr(hit, "retrieval_method", "vector"),
                        "title": row["title"],
                        "source_url": row["source_url"],
                        "document_type": row["document_type"],
                        "chunk_type": row["chunk_type"],
                        "section_path": row["section_path"],
                        "text": row["display_text"],
                        "source_pages": row["metadata_json"].get("source_pages", []),
                        "asset_ids": asset_ids,
                        "assets": [{**a, "id": str(a["id"])} for a in asset_rows],
                        "scope_note": row["metadata_json"].get("scope_note", ""),
                        "software_context": row["metadata_json"].get("software_context"),
                    }
                )
    return {"question": question, "evidence": evidence}
