# mypy: ignore-errors
"""initial intelligence rag schema

Revision ID: 0001_initial_intelligence_rag_schema
Revises:
Create Date: 2026-06-14 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_intelligence_rag_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_sites",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("site_type", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("allowed_domains", postgresql.JSONB(), nullable=False),
        sa.Column("fetch_mode", sa.String(length=40), nullable=False),
        sa.Column("default_language", sa.String(length=16), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crawl_jobs",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("trigger_mode", sa.String(length=40), nullable=False),
        sa.Column("cron_expr", sa.String(length=120), nullable=True),
        sa.Column("seed_config_json", postgresql.JSONB(), nullable=False),
        sa.Column("parser_profile", sa.String(length=80), nullable=False),
        sa.Column("max_pages", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("agent_policy_json", postgresql.JSONB(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_site_id"], ["source_sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crawl_runs",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_type", sa.String(length=40), nullable=False),
        sa.Column("execution_mode", sa.String(length=40), nullable=False),
        sa.Column("seed_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("parsed_count", sa.Integer(), nullable=False),
        sa.Column("extracted_count", sa.Integer(), nullable=False),
        sa.Column("deduped_count", sa.Integer(), nullable=False),
        sa.Column("chunked_count", sa.Integer(), nullable=False),
        sa.Column("embedded_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("config_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["crawl_job_id"], ["crawl_jobs.id"]),
        sa.ForeignKeyConstraint(["source_site_id"], ["source_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "raw_pages",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("response_headers_json", postgresql.JSONB(), nullable=False),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetch_error", sa.Text(), nullable=True),
        sa.Column("parser_profile", sa.String(length=80), nullable=True),
        sa.Column("parse_status", sa.String(length=40), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["crawl_run_id"], ["crawl_runs.id"]),
        sa.ForeignKeyConstraint(["source_site_id"], ["source_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "authors",
        sa.Column("source_site_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("handle", sa.String(length=255), nullable=True),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column("external_author_id", sa.String(length=255), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_site_id"], ["source_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
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
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("dedup_key", sa.String(length=512), nullable=False),
        sa.Column("search_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["authors.id"]),
        sa.ForeignKeyConstraint(["crawl_run_id"], ["crawl_runs.id"]),
        sa.ForeignKeyConstraint(["parent_item_id"], ["content_items.id"]),
        sa.ForeignKeyConstraint(["raw_page_id"], ["raw_pages.id"]),
        sa.ForeignKeyConstraint(["source_site_id"], ["source_sites.id"]),
        sa.ForeignKeyConstraint(["thread_root_id"], ["content_items.id"]),
        sa.PrimaryKeyConstraint("id"),
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
        sa.Column("chunk_metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("qdrant_point_id", sa.String(length=255), nullable=True),
        sa.Column("embed_status", sa.String(length=40), nullable=False),
        sa.Column("embed_error", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_item_id"], ["content_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "entities",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "search_queries",
        sa.Column("raw_query", sa.Text(), nullable=False),
        sa.Column("filter_json", postgresql.JSONB(), nullable=False),
        sa.Column("mode", sa.String(length=40), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("used_agent", sa.Boolean(), nullable=False),
        sa.Column("optimized_query", sa.Text(), nullable=True),
        sa.Column("keyword_terms_json", postgresql.JSONB(), nullable=False),
        sa.Column("entity_hints_json", postgresql.JSONB(), nullable=False),
        sa.Column("time_hints_json", postgresql.JSONB(), nullable=False),
        sa.Column("result_summary_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
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
        sa.Column("counters_json", postgresql.JSONB(), nullable=False),
        sa.Column("agent_trace_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["crawl_run_id"], ["crawl_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_content_item_id"], ["content_items.id"]),
        sa.ForeignKeyConstraint(["related_raw_page_id"], ["raw_pages.id"]),
        sa.PrimaryKeyConstraint("id"),
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
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_chunk_id"], ["content_chunks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"], ["content_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
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
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_summary_json", postgresql.JSONB(), nullable=False),
        sa.Column("output_summary_json", postgresql.JSONB(), nullable=False),
        sa.Column("model_provider", sa.String(length=80), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["related_content_item_id"], ["content_items.id"]),
        sa.ForeignKeyConstraint(["related_crawl_run_id"], ["crawl_runs.id"]),
        sa.ForeignKeyConstraint(["related_raw_page_id"], ["raw_pages.id"]),
        sa.ForeignKeyConstraint(["related_search_query_id"], ["search_queries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


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
