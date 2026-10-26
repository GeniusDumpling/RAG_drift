"""Select connected, richly linked subgraphs before applying display budgets."""

import heapq
from collections import defaultdict

from app.schemas.video_graph import GraphData, GraphEdge

DEFAULT_MIN_COMPONENT_SIZE = 5


def connected_display_graph(
    graph: GraphData,
    *,
    min_component_size: int = DEFAULT_MIN_COMPONENT_SIZE,
    max_nodes: int | None = 300,
    max_edges: int | None = 500,
) -> GraphData:
    """Use distinct neighbors for ranking; parallel claims cannot inflate connectivity.

    Grow each component from its most interconnected core. Reserve a spanning tree
    before filling remaining edge capacity, so limiting edges cannot strand nodes.
    This only selects a view; evidence and the underlying graph remain untouched.
    """
    nodes = {node.id: node for node in graph.nodes}
    neighbors: dict[str, set[str]] = {key: set() for key in nodes}
    pairs: dict[tuple[str, str], list[GraphEdge]] = defaultdict(list)
    valid_edges = [e for e in graph.edges if e.source in nodes and e.target in nodes]
    for edge in valid_edges:
        if edge.source != edge.target:
            neighbors[edge.source].add(edge.target)
            neighbors[edge.target].add(edge.source)
        pairs[tuple(sorted((edge.source, edge.target)))].append(edge)
    for edges in pairs.values():
        edges.sort(key=lambda edge: (-edge.evidence_count, edge.id))

    # Degeneracy peeling gives each node its k-core number in O((V + E) log V).
    degree = {key: len(adjacent) for key, adjacent in neighbors.items()}
    heap = [(value, key) for key, value in degree.items()]
    heapq.heapify(heap)
    cores: dict[str, int] = {}
    while heap:
        value, key = heapq.heappop(heap)
        if key in cores or value != degree[key]:
            continue
        cores[key] = value
        for neighbor in neighbors[key]:
            if neighbor not in cores and degree[neighbor] > value:
                degree[neighbor] -= 1
                heapq.heappush(heap, (degree[neighbor], neighbor))

    remaining = set(nodes)
    components = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component, stack = {seed}, [seed]
        while stack:
            for neighbor in neighbors[stack.pop()] & remaining:
                remaining.remove(neighbor)
                component.add(neighbor)
                stack.append(neighbor)
        if len(component) >= min_component_size:
            components.append(component)
    components.sort(key=lambda component: (
        -max(cores[key] for key in component),
        -sum(cores[key] >= 2 for key in component),
        -sum(len(neighbors[key]) for key in component),
        -len(component),
        min(component),
    ))

    chosen: dict[str, None] = {}
    spanning: dict[str, GraphEdge] = {}

    def offer(
        key: str,
        local: set[str],
        attached: dict[str, int],
        frontier: list[tuple[int, int, int, str]],
    ) -> None:
        for neighbor in neighbors[key] - local:
            attached[neighbor] += 1
            heapq.heappush(frontier, (
                -attached[neighbor], -cores[neighbor], -len(neighbors[neighbor]), neighbor,
            ))

    for component in components:
        capacity = len(component)
        if max_nodes is not None:
            capacity = min(capacity, max_nodes - len(chosen))
        if max_edges is not None:
            capacity = min(capacity, max_edges - len(spanning) + 1)
        if capacity < min_component_size:
            continue
        seed = min(component, key=lambda key: (-cores[key], -len(neighbors[key]), key))
        local = {seed}
        order = [seed]
        local_spanning = []
        attached: dict[str, int] = defaultdict(int)
        frontier: list[tuple[int, int, int, str]] = []

        offer(seed, local, attached, frontier)
        while frontier and len(local) < capacity:
            count, _, _, key = heapq.heappop(frontier)
            if key in local or -count != attached[key]:
                continue
            # One real edge to an already selected neighbor guarantees connectivity.
            edge = min(
                (pairs[tuple(sorted((key, other)))][0] for other in neighbors[key] & local),
                key=lambda edge: (-edge.evidence_count, edge.id),
            )
            local_spanning.append(edge)
            local.add(key)
            order.append(key)
            offer(key, local, attached, frontier)
        chosen.update(dict.fromkeys(order))
        spanning.update((edge.id, edge) for edge in local_spanning)

    # Prefer links inside the selected core; include assertions and qualifiers verbatim.
    extras = sorted(
        (edge for edge in valid_edges if edge.id not in spanning
         and edge.source in chosen and edge.target in chosen),
        key=lambda edge: (
            -min(cores[edge.source], cores[edge.target]),
            -(len(neighbors[edge.source]) + len(neighbors[edge.target])),
            -edge.evidence_count, edge.id,
        ),
    )
    capacity = None if max_edges is None else max_edges - len(spanning)
    selected_edges = [*spanning.values(), *extras[:capacity]]
    return GraphData(
        nodes=[nodes[key] for key in chosen],
        edges=selected_edges,
        truncated=(graph.truncated or len(chosen) < len(graph.nodes)
                   or len(selected_edges) < len(graph.edges)),
    )
