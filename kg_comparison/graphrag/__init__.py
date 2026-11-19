"""GraphRAG baseline adapter package for kg_comparison."""

from typing import Any

from kg_comparison.graphrag.engine import (
    GraphRAGAdapter,
    GraphRAGConfig,
    extract_case_library,
    extract_case_native,
    merge_graphrag_corpus,
    post_map_case,
)


def __getattr__(name: str) -> Any:
    if name == "run_experiment":
        from kg_comparison.graphrag.run import run_experiment

        return run_experiment
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "GraphRAGAdapter",
    "GraphRAGConfig",
    "extract_case_library",
    "extract_case_native",
    "merge_graphrag_corpus",
    "post_map_case",
    "run_experiment",
]
