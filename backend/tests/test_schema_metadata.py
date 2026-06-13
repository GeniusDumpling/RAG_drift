from collections.abc import Iterable
from typing import Any, cast

import app.models  # noqa: F401
from app.db.base import Base, utcnow
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

APPLICATION_TABLES = {
    "source_sites",
    "crawl_jobs",
    "crawl_runs",
    "crawl_run_events",
    "raw_pages",
    "authors",
    "content_items",
    "content_chunks",
    "entities",
    "content_entity_mentions",
    "search_queries",
    "agent_calls",
}


def _unique_constraint_column_sets(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _indexes_for_columns(table_name: str, column_names: Iterable[str]) -> list[Index]:
    expected = tuple(column_names)
    table = Base.metadata.tables[table_name]
    return [
        index
        for index in table.indexes
        if tuple(column.name for column in index.columns) == expected
    ]


def test_expected_application_tables_exist_exactly() -> None:
    assert set(Base.metadata.tables) == APPLICATION_TABLES
    assert len(Base.metadata.tables) == 12


def test_stable_naming_convention_is_configured() -> None:
    convention = Base.metadata.naming_convention
    assert convention["pk"] == "pk_%(table_name)s"
    assert convention["fk"] == "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s"
    assert convention["uq"] == "uq_%(table_name)s_%(column_0_N_name)s"
    assert convention["ix"] == "ix_%(table_name)s_%(column_0_N_name)s"


def test_content_item_has_thread_and_search_columns() -> None:
    table = Base.metadata.tables["content_items"]
    for column in [
        "parent_item_id",
        "thread_root_id",
        "raw_text",
        "cleaned_text",
        "summary_text",
        "tags",
        "search_tsv",
    ]:
        assert column in table.c


def test_important_postgresql_types_are_used() -> None:
    content_items = Base.metadata.tables["content_items"]
    search_queries = Base.metadata.tables["search_queries"]

    assert isinstance(content_items.c.tags.type, JSONB)
    assert isinstance(content_items.c.metadata_json.type, JSONB)
    assert isinstance(search_queries.c.filter_json.type, JSONB)
    assert isinstance(content_items.c.search_tsv.type, TSVECTOR)


def test_critical_nullability_and_defaults_are_declared() -> None:
    crawl_runs = Base.metadata.tables["crawl_runs"]
    crawl_run_events = Base.metadata.tables["crawl_run_events"]
    content_chunks = Base.metadata.tables["content_chunks"]
    search_queries = Base.metadata.tables["search_queries"]

    assert crawl_runs.c.created_at.nullable is False
    crawl_runs_created_at_default = cast(Any, crawl_runs.c.created_at.default)
    assert crawl_runs_created_at_default is not None
    assert crawl_runs_created_at_default.is_callable
    assert crawl_runs_created_at_default.arg.__name__ == utcnow.__name__
    assert crawl_runs.c.created_at.server_default is not None

    assert crawl_run_events.c.created_at.nullable is False
    crawl_run_events_created_at_default = cast(Any, crawl_run_events.c.created_at.default)
    assert crawl_run_events_created_at_default is not None
    assert crawl_run_events_created_at_default.is_callable
    assert crawl_run_events_created_at_default.arg.__name__ == utcnow.__name__
    assert crawl_run_events.c.created_at.server_default is not None

    assert content_chunks.c.embed_status.nullable is False
    embed_status_default = cast(Any, content_chunks.c.embed_status.default)
    assert embed_status_default is not None
    assert embed_status_default.arg == "pending"
    assert content_chunks.c.embed_status.server_default is not None

    assert search_queries.c.used_agent.nullable is False
    used_agent_default = cast(Any, search_queries.c.used_agent.default)
    assert used_agent_default is not None
    assert used_agent_default.arg is False
    assert search_queries.c.used_agent.server_default is not None


def test_content_chunk_order_is_unique_per_content_item() -> None:
    unique_column_sets = _unique_constraint_column_sets("content_chunks")
    assert ("content_item_id", "chunk_index") in unique_column_sets


def test_content_item_search_tsv_has_gin_index() -> None:
    indexes = _indexes_for_columns("content_items", ["search_tsv"])
    assert indexes
    assert any(
        index.dialect_options["postgresql"].get("using") == "gin" for index in indexes
    )


def test_agent_calls_links_to_supported_workflows() -> None:
    table = Base.metadata.tables["agent_calls"]
    for column in [
        "agent_role",
        "caller",
        "related_crawl_run_id",
        "related_raw_page_id",
        "related_content_item_id",
        "related_search_query_id",
        "input_summary_json",
        "output_summary_json",
    ]:
        assert column in table.c
