"""KGGen baseline adapter package for kg_comparison."""

from typing import Any

from kg_comparison.kggen.engine import (
    KGGenAdapter,
    KGGenConfig,
    aggregate_case_predictions,
    cluster_aggregated_graph,
    extract_case_library,
    extract_case_native,
)


def __getattr__(name: str) -> Any:
    if name == "run_experiment":
        from kg_comparison.kggen.run import run_experiment

        return run_experiment
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "KGGenAdapter",
    "KGGenConfig",
    "aggregate_case_predictions",
    "cluster_aggregated_graph",
    "extract_case_library",
    "extract_case_native",
    "run_experiment",
]
