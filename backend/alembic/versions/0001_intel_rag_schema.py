# mypy: ignore-errors
"""initial intelligence rag schema

Revision ID: 0001_intel_rag_schema
Revises:
Create Date: 2026-06-14 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_intel_rag_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TIMESTAMP_DEFAULT = sa.text("now()")
_JSONB_OBJECT_DEFAULT = sa.text("'{}'::jsonb")
_JSONB_ARRAY_DEFAULT = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "source_sites",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("site_type", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column(
            "allowed_domains",
            postgresql.JSONB(),
            server_default=_JSONB_ARRAY_DEFAULT,
            nullable=False,
        ),
        sa.Column("fetch_mode", sa.String(length=40), nullable=False),
        sa.Column("default_language", sa.String(length=16), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_sites")),
    )
    op.create_table(
        "crawl_jobs",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("trigger_mode", sa.String(length=40), nullable=False),
        sa.Column("cron_expr", sa.String(length=120), nullable=True),
        sa.Column(
            "seed_config_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("parser_profile", sa.String(length=80), nullable=False),
        sa.Column("max_pages", sa.Integer(), server_default=sa.text("20"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "agent_policy_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_site_id"],
            ["source_sites.id"],
            name=op.f("fk_crawl_jobs_source_site_id_source_sites"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_jobs")),
    )
    op.create_index(
        op.f("ix_crawl_jobs_source_site_id"), "crawl_jobs", ["source_site_id"], unique=False
    )
    op.create_table(
        "crawl_runs",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_type", sa.String(length=40), nullable=False),
        sa.Column(
            "execution_mode",
            sa.String(length=40),
            server_default=sa.text("'agent_assisted'"),
            nullable=False,
        ),
        sa.Column("seed_url", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=40), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discovered_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("parsed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("extracted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deduped_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("chunked_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("embedded_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "config_snapshot_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["crawl_job_id"],
            ["crawl_jobs.id"],
            name=op.f("fk_crawl_runs_crawl_job_id_crawl_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["source_site_id"],
            ["source_sites.id"],
            name=op.f("fk_crawl_runs_source_site_id_source_sites"),
        ),
        sa.CheckConstraint(
            "discovered_count >= 0", name=op.f("ck_crawl_runs_discovered_count_non_negative")
        ),
        sa.CheckConstraint(
            "fetched_count >= 0", name=op.f("ck_crawl_runs_fetched_count_non_negative")
        ),
        sa.CheckConstraint(
            "parsed_count >= 0", name=op.f("ck_crawl_runs_parsed_count_non_negative")
        ),
        sa.CheckConstraint(
            "extracted_count >= 0", name=op.f("ck_crawl_runs_extracted_count_non_negative")
        ),
        sa.CheckConstraint(
            "deduped_count >= 0", name=op.f("ck_crawl_runs_deduped_count_non_negative")
        ),
        sa.CheckConstraint(
            "chunked_count >= 0", name=op.f("ck_crawl_runs_chunked_count_non_negative")
        ),
        sa.CheckConstraint(
            "embedded_count >= 0", name=op.f("ck_crawl_runs_embedded_count_non_negative")
        ),
        sa.CheckConstraint(
            "error_count >= 0", name=op.f("ck_crawl_runs_error_count_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_runs")),
    )
    op.create_index(
        op.f("ix_crawl_runs_source_site_id"), "crawl_runs", ["source_site_id"], unique=False
    )
    op.create_index(
        op.f("ix_crawl_runs_crawl_job_id"), "crawl_runs", ["crawl_job_id"], unique=False
    )
    op.create_index(op.f("ix_crawl_runs_status"), "crawl_runs", ["status"], unique=False)
    op.create_table(
        "raw_pages",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column(
            "response_headers_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column(
            "raw_json", postgresql.JSONB(), server_default=_JSONB_OBJECT_DEFAULT, nullable=False
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetch_error", sa.Text(), nullable=True),
        sa.Column("parser_profile", sa.String(length=80), nullable=True),
        sa.Column("extraction_method", sa.String(length=80), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column(
            "parse_status",
            sa.String(length=40),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("body_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_raw_pages_crawl_run_id_crawl_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["source_site_id"],
            ["source_sites.id"],
            name=op.f("fk_raw_pages_source_site_id_source_sites"),
        ),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR "
            "extraction_confidence >= 0 AND "
            "extraction_confidence <= 1",
            name=op.f("ck_raw_pages_extraction_confidence_unit_interval"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_pages")),
    )
    op.create_index(
        op.f("ix_raw_pages_source_site_id"), "raw_pages", ["source_site_id"], unique=False
    )
    op.create_index(
        op.f("ix_raw_pages_crawl_run_id"), "raw_pages", ["crawl_run_id"], unique=False
    )
    op.create_index(op.f("ix_raw_pages_body_hash"), "raw_pages", ["body_hash"], unique=False)
    op.create_table(
        "authors",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("handle", sa.String(length=255), nullable=True),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column("external_author_id", sa.String(length=255), nullable=True),
        sa.Column(
            "raw_json", postgresql.JSONB(), server_default=_JSONB_OBJECT_DEFAULT, nullable=False
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_site_id"],
            ["source_sites.id"],
            name=op.f("fk_authors_source_site_id_source_sites"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authors")),
    )
    op.create_index(op.f("ix_authors_source_site_id"), "authors", ["source_site_id"], unique=False)
    op.create_table(
        "content_items",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("thread_root_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("cleaned_text", sa.Text(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("structured_by", sa.String(length=80), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), server_default=_JSONB_ARRAY_DEFAULT, nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("dedup_key", sa.String(length=512), nullable=False),
        sa.Column("search_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["authors.id"], name=op.f("fk_content_items_author_id_authors")
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_content_items_crawl_run_id_crawl_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_item_id"],
            ["content_items.id"],
            name=op.f("fk_content_items_parent_item_id_content_items"),
        ),
        sa.ForeignKeyConstraint(
            ["raw_page_id"],
            ["raw_pages.id"],
            name=op.f("fk_content_items_raw_page_id_raw_pages"),
        ),
        sa.ForeignKeyConstraint(
            ["source_site_id"],
            ["source_sites.id"],
            name=op.f("fk_content_items_source_site_id_source_sites"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_root_id"],
            ["content_items.id"],
            name=op.f("fk_content_items_thread_root_id_content_items"),
        ),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR "
            "extraction_confidence >= 0 AND "
            "extraction_confidence <= 1",
            name=op.f("ck_content_items_extraction_confidence_unit_interval"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_items")),
        sa.UniqueConstraint("dedup_key", name=op.f("uq_content_items_dedup_key")),
    )
    op.create_index(
        op.f("ix_content_items_source_site_id"),
        "content_items",
        ["source_site_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_items_raw_page_id"), "content_items", ["raw_page_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_items_crawl_run_id"), "content_items", ["crawl_run_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_items_author_id"), "content_items", ["author_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_items_parent_item_id"),
        "content_items",
        ["parent_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_items_thread_root_id"),
        "content_items",
        ["thread_root_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_items_item_type"), "content_items", ["item_type"], unique=False
    )
    op.create_index(
        "ix_content_items_search_tsv_gin",
        "content_items",
        ["search_tsv"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "content_chunks",
        sa.Column("content_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("embed_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "chunk_metadata_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("qdrant_point_id", sa.String(length=255), nullable=True),
        sa.Column(
            "embed_status",
            sa.String(length=40),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("embed_error", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name=op.f("fk_content_chunks_content_item_id_content_items"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0", name=op.f("ck_content_chunks_chunk_index_non_negative")
        ),
        sa.CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name=op.f("ck_content_chunks_token_count_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_chunks")),
        sa.UniqueConstraint(
            "content_item_id",
            "chunk_index",
            name=op.f("uq_content_chunks_content_item_id_chunk_index"),
        ),
    )
    op.create_table(
        "entities",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entities")),
    )
    op.create_table(
        "search_queries",
        sa.Column("raw_query", sa.Text(), nullable=False),
        sa.Column(
            "filter_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=40), server_default=sa.text("'hybrid'"), nullable=False),
        sa.Column("top_k", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("used_agent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("optimized_query", sa.Text(), nullable=True),
        sa.Column(
            "keyword_terms_json",
            postgresql.JSONB(),
            server_default=_JSONB_ARRAY_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "entity_hints_json",
            postgresql.JSONB(),
            server_default=_JSONB_ARRAY_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "time_hints_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "result_summary_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint("top_k > 0", name=op.f("ck_search_queries_top_k_positive")),
        sa.CheckConstraint(
            "result_count IS NULL OR result_count >= 0",
            name=op.f("ck_search_queries_result_count_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_queries")),
    )
    op.create_index(
        op.f("ix_search_queries_created_at"), "search_queries", ["created_at"], unique=False
    )
    op.create_table(
        "crawl_run_events",
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("related_url", sa.Text(), nullable=True),
        sa.Column("related_raw_page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("related_content_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "counters_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "agent_trace_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_crawl_run_events_crawl_run_id_crawl_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_content_item_id"],
            ["content_items.id"],
            name=op.f("fk_crawl_run_events_related_content_item_id_content_items"),
        ),
        sa.ForeignKeyConstraint(
            ["related_raw_page_id"],
            ["raw_pages.id"],
            name=op.f("fk_crawl_run_events_related_raw_page_id_raw_pages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_run_events")),
    )
    op.create_index(
        op.f("ix_crawl_run_events_crawl_run_id"),
        "crawl_run_events",
        ["crawl_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_run_events_related_raw_page_id"),
        "crawl_run_events",
        ["related_raw_page_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_run_events_related_content_item_id"),
        "crawl_run_events",
        ["related_content_item_id"],
        unique=False,
    )
    op.create_table(
        "content_entity_mentions",
        sa.Column("content_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("surface_text", sa.String(length=255), nullable=True),
        sa.Column("start_char", sa.Integer(), nullable=True),
        sa.Column("end_char", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("extraction_method", sa.String(length=80), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence >= 0 AND confidence <= 1",
            name=op.f("ck_content_entity_mentions_confidence_unit_interval"),
        ),
        sa.ForeignKeyConstraint(
            ["content_chunk_id"],
            ["content_chunks.id"],
            name=op.f("fk_content_entity_mentions_content_chunk_id_content_chunks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name=op.f("fk_content_entity_mentions_content_item_id_content_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_content_entity_mentions_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_entity_mentions")),
    )
    op.create_index(
        op.f("ix_content_entity_mentions_content_item_id"),
        "content_entity_mentions",
        ["content_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_entity_mentions_entity_id"),
        "content_entity_mentions",
        ["entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_entity_mentions_content_chunk_id"),
        "content_entity_mentions",
        ["content_chunk_id"],
        unique=False,
    )
    op.create_table(
        "agent_calls",
        sa.Column("agent_role", sa.String(length=80), nullable=False),
        sa.Column("caller", sa.String(length=120), nullable=False),
        sa.Column("related_crawl_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("related_raw_page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("related_content_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("related_search_query_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_schema_version", sa.String(length=40), nullable=False),
        sa.Column("response_schema_version", sa.String(length=40), nullable=False),
        sa.Column(
            "status", sa.String(length=40), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column(
            "input_summary_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "output_summary_json",
            postgresql.JSONB(),
            server_default=_JSONB_OBJECT_DEFAULT,
            nullable=False,
        ),
        sa.Column("model_provider", sa.String(length=80), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "trace_json", postgresql.JSONB(), server_default=_JSONB_OBJECT_DEFAULT, nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name=op.f("ck_agent_calls_latency_ms_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["related_content_item_id"],
            ["content_items.id"],
            name=op.f("fk_agent_calls_related_content_item_id_content_items"),
        ),
        sa.ForeignKeyConstraint(
            ["related_crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_agent_calls_related_crawl_run_id_crawl_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["related_raw_page_id"],
            ["raw_pages.id"],
            name=op.f("fk_agent_calls_related_raw_page_id_raw_pages"),
        ),
        sa.ForeignKeyConstraint(
            ["related_search_query_id"],
            ["search_queries.id"],
            name=op.f("fk_agent_calls_related_search_query_id_search_queries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_calls")),
    )
    op.create_index(
        op.f("ix_agent_calls_related_crawl_run_id"),
        "agent_calls",
        ["related_crawl_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_calls_related_raw_page_id"),
        "agent_calls",
        ["related_raw_page_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_calls_related_content_item_id"),
        "agent_calls",
        ["related_content_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_calls_related_search_query_id"),
        "agent_calls",
        ["related_search_query_id"],
        unique=False,
    )
    op.create_index(op.f("ix_agent_calls_status"), "agent_calls", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("agent_calls")
    op.drop_table("content_entity_mentions")
    op.drop_table("crawl_run_events")
    op.drop_table("search_queries")
    op.drop_table("entities")
    op.drop_table("content_chunks")
    op.drop_table("content_items")
    op.drop_table("authors")
    op.drop_table("raw_pages")
    op.drop_table("crawl_runs")
    op.drop_table("crawl_jobs")
    op.drop_table("source_sites")
