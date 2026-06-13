import app.models  # noqa: F401
from app.db.base import Base


def test_expected_tables_exist() -> None:
    expected = {
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
    assert expected.issubset(set(Base.metadata.tables))


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
