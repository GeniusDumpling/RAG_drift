"""Display selection must favor connectivity without changing stored claims."""

import random
from collections import Counter
from itertools import combinations

import pytest
from app.schemas.video_graph import GraphData, GraphEdge, GraphNode
from app.services.graph_display import connected_display_graph
from app.services.neo4j_graph.queries import GraphQueries

pytestmark = pytest.mark.no_db


def graph_for(pairs, *, isolated=()):
    names = sorted({name for pair in pairs for name in pair} | set(isolated))
    return GraphData(
        nodes=[GraphNode(id=name, label=name, kind="entity", entity_type="technology")
               for name in names],
        edges=[GraphEdge(
            id=f"r{i:04}", source=source, target=target, predicate="uses", kind="relation",
            evidence_count=i + 1, assertions={"asserted": i, "uncertain": 1},
            qualifiers={"source": str(i)},
        ) for i, (source, target) in enumerate(pairs)],
    )


def component_sizes(graph):
    adjacent = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        assert edge.source in adjacent and edge.target in adjacent
        adjacent[edge.source].add(edge.target)
        adjacent[edge.target].add(edge.source)
    remaining = set(adjacent)
    sizes = []
    while remaining:
        stack = [remaining.pop()]
        size = 1
        while stack:
            for neighbor in adjacent[stack.pop()] & remaining:
                remaining.remove(neighbor)
                stack.append(neighbor)
                size += 1
        sizes.append(size)
    return sizes


def test_filters_tiny_components_before_they_can_consume_the_budget():
    core = list(combinations([f"core{i}" for i in range(6)], 2))
    noise = [(f"pair{i}a", f"pair{i}b") for i in range(100)]
    triples = [("tiny1", "tiny2"), ("tiny2", "tiny3"), ("tiny3", "tiny4")]
    graph = graph_for([*core, *noise, *triples], isolated=["alone"])
    selected = connected_display_graph(graph, max_nodes=6, max_edges=15)
    assert {node.id for node in selected.nodes} == {f"core{i}" for i in range(6)}
    assert len(selected.edges) == 15
    assert component_sizes(selected) == [6]
    assert selected.truncated


def test_dense_core_wins_over_a_larger_but_sparsely_connected_component():
    clique = list(combinations([f"dense{i}" for i in range(5)], 2))
    chain = [(f"sparse{i}", f"sparse{i+1}") for i in range(80)]
    graph = graph_for([*clique, *chain])
    selected = connected_display_graph(graph, max_nodes=5, max_edges=10)
    assert all(node.id.startswith("dense") for node in selected.nodes)
    assert len(selected.edges) == 10


def test_hub_neighborhood_wins_over_unconnected_pairs_with_more_evidence():
    star = [("hub", f"leaf{i}") for i in range(30)]
    graph = graph_for([*star, *[(f"a{i}", f"b{i}") for i in range(80)]])
    selected = connected_display_graph(graph, max_nodes=10, max_edges=9)
    assert selected.nodes[0].id == "hub"
    assert component_sizes(selected) == [10]


@pytest.mark.parametrize("max_nodes,max_edges", [(300, 500), (150, 300), (12, 4), (6, 2)])
def test_budgets_do_not_fragment_a_connected_component(max_nodes, max_edges):
    graph = graph_for([(f"n{i}", f"n{i+1}") for i in range(600)])
    selected = connected_display_graph(graph, max_nodes=max_nodes, max_edges=max_edges)
    assert len(selected.nodes) <= max_nodes and len(selected.edges) <= max_edges
    assert all(size >= 5 for size in component_sizes(selected))
    if max_edges >= 4:
        assert component_sizes(selected) == [min(601, max_nodes, max_edges + 1)]
    else:
        assert not selected.nodes and not selected.edges
    assert selected.truncated


def test_parallel_and_self_loop_edges_do_not_make_a_two_node_component_eligible():
    graph = graph_for([*(list(combinations([f"core{i}" for i in range(5)], 2))),
                       *[("a", "b")] * 100, *[("alone", "alone")] * 100])
    selected = connected_display_graph(graph)
    assert component_sizes(selected) == [5]
    assert len(selected.edges) == 10


def test_claims_are_preserved_and_selection_does_not_depend_on_database_row_order():
    graph = graph_for(list(combinations([f"n{i}" for i in range(12)], 2)))
    original = graph.model_dump()
    selected = connected_display_graph(graph, max_nodes=8, max_edges=14)
    shuffled = graph.model_copy(deep=True)
    random.Random(42).shuffle(shuffled.nodes)
    random.Random(43).shuffle(shuffled.edges)
    assert connected_display_graph(shuffled, max_nodes=8, max_edges=14) == selected
    assert graph.model_dump() == original
    originals = {edge.id: edge for edge in graph.edges}
    assert all(edge == originals[edge.id] for edge in selected.edges)
    assert component_sizes(selected) == [8]


def test_keeps_multiple_substantial_components_without_filling_a_tiny_remainder():
    graph = graph_for([(f"{group}{i}", f"{group}{i+1}")
                       for group in ["a", "b", "c"] for i in range(5)])
    selected = connected_display_graph(graph, max_nodes=14, max_edges=12)
    assert Counter(component_sizes(selected)) == {6: 2}
    assert selected.truncated


@pytest.mark.parametrize("graph", [GraphData(nodes=[], edges=[]),
                                  graph_for([("a", "b")], isolated=["alone"])])
def test_empty_or_only_tiny_candidates_return_an_empty_view(graph):
    selected = connected_display_graph(graph)
    assert not selected.nodes and not selected.edges


def test_unbounded_selection_retains_all_eligible_components():
    graph = graph_for([(f"{group}{i}", f"{group}{i+1}")
                       for group in ["a", "b"] for i in range(5)])
    selected = connected_display_graph(graph, max_nodes=None, max_edges=None)
    assert len(selected.nodes) == 12 and len(selected.edges) == 10
    assert not selected.truncated


def test_neo4j_selection_considers_candidates_before_applying_display_caps():
    graph = graph_for([*[(f"tiny{i}a", f"tiny{i}b") for i in range(20)],
                       *list(combinations([f"core{i}" for i in range(6)], 2))],
                      isolated=["alone"])

    class Store:
        def read(self, cypher, **params):
            if "RETURN DISTINCT e" in cypher:
                assert params["q"] == "px4"
                assert params["kind"] == "technology"
                return [{"e": {"id": node.id, "canonical_name": node.label,
                               "entity_type": node.entity_type}} for node in graph.nodes]
            assert "RETURN r,s,o" in cypher
            assert params["assertion"] == "uncertain"
            assert params["kind"] == "technology"
            entities = {node.id: {"id": node.id, "canonical_name": node.label,
                                  "entity_type": node.entity_type} for node in graph.nodes}
            return [{"r": {"id": edge.id, "predicate": edge.predicate},
                     "s": entities[edge.source], "o": entities[edge.target],
                     "count": 1, "asserted": 0, "uncertain": 1, "negated": 0}
                    for edge in graph.edges]

    selected = GraphQueries(Store()).get_graph(
        None, q="PX4", entity_type="technology", assertion="uncertain",
        min_component_size=5, max_nodes=6, max_edges=15,
    )
    assert len(selected.edges) == 15 and component_sizes(selected) == [6]
    assert all(node.id.startswith("core") for node in selected.nodes)
    assert all(edge.assertions["uncertain"] == 1 for edge in selected.edges)
