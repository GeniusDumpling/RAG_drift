"""Full graph browsing must not silently retain the old node/edge caps."""

import uuid
from unittest.mock import patch

import httpx
import pytest
from app.api import video_graph as api
from app.schemas.video_graph import GraphData
from app.services.video_graph.queries import GraphQueries
from fastapi import FastAPI

pytestmark = pytest.mark.no_db


class GraphFixtureStore:
    def __init__(self):
        self.entities = [
            dict(id=str(uuid.uuid4()), canonical_name=f"Entity {i}", entity_type="technology")
            for i in range(430)
        ]
        self.rows = [
            dict(
                r=dict(id=str(uuid.uuid4()), predicate="uses"),
                s=self.entities[i],
                o=self.entities[i + 1],
                count=2,
                asserted=1,
                uncertain=1,
                negated=0,
            )
            for i in range(419)
        ]

    def read(self, cypher, **params):
        if "RETURN DISTINCT e" in cypher:
            return [dict(e=e) for e in self.entities]
        if "RETURN r,s,o" in cypher:
            return self.rows
        raise AssertionError(cypher)


def test_full_graph_includes_all_relations_and_isolated_entities():
    store = GraphFixtureStore()
    queries = GraphQueries(store)
    bounded = queries.get_graph(None)
    assert len(bounded.nodes) <= 80 and bounded.truncated
    full = queries.get_graph(None, max_nodes=None, max_edges=None)
    assert len(full.nodes) == 430
    assert len(full.edges) == 419
    assert not full.truncated
    ids = {node.id for node in full.nodes}
    assert all(e.source in ids and e.target in ids for e in full.edges)
    assert all(e.evidence_count == 2 and e.assertions["uncertain"] == 1 for e in full.edges)


class CategoryFixtureStore:
    """Active entities with isolated products, mixed-type neighbors and a three-hop chain."""

    def __init__(self):
        def entity(name, kind):
            return dict(id=str(uuid.uuid4()), canonical_name=name, entity_type=kind)

        self.products = [entity(f"Product {i}", "product") for i in range(605)]
        self.technology = entity("Technology", "technology")
        self.component = entity("Component", "component")
        self.second = entity("Second hop", "software")
        self.third = entity("Third hop", "method")
        self.entities = [*self.products, self.technology, self.component, self.second, self.third]
        pairs = [
            (self.products[0], self.technology),
            (self.component, self.products[0]),
            (self.technology, self.second),
            (self.second, self.third),
        ]
        self.rows = [
            dict(r=dict(id=str(uuid.uuid4()), predicate="uses"), s=s, o=o,
                 count=4, asserted=2, uncertain=1, negated=1)
            for s, o in pairs
        ]

    def read(self, cypher, **params):
        if "RETURN DISTINCT e" in cypher:
            return [dict(e=e) for e in self.entities
                    if (not params.get("kind") or e["entity_type"] == params["kind"])
                    and (not params.get("eid") or e["id"] == params["eid"])]
        if "RETURN r,s,o" in cypher:
            rows = [row for row in self.rows if row["s"]["id"] in params["frontier"]
                    or row["o"]["id"] in params["frontier"]]
            if "s.entity_type=$kind" in cypher and params.get("kind"):
                rows = [row for row in rows if row["s"]["entity_type"] == params["kind"]
                        and row["o"]["entity_type"] == params["kind"]]
            return rows
        raise AssertionError(cypher)


@pytest.mark.parametrize("hops,neighbors,edge_count", [(1, 2, 2), (2, 3, 3)])
def test_category_includes_every_seed_and_cross_type_neighbors_at_requested_depth(
    hops, neighbors, edge_count,
):
    store = CategoryFixtureStore()
    graph = GraphQueries(store).get_graph(
        None, entity_type="product", hops=hops, max_nodes=None, max_edges=None,
    )
    ids = {node.id for node in graph.nodes}
    assert len(graph.nodes) == len(store.products) + neighbors
    assert all(product["id"] in ids for product in store.products)
    assert store.technology["id"] in ids and store.component["id"] in ids
    assert (store.second["id"] in ids) == (hops == 2)
    assert store.third["id"] not in ids
    assert len(graph.edges) == edge_count
    assert not graph.truncated
    assert all(edge.assertions == {"asserted": 2, "uncertain": 1, "negated": 1}
               for edge in graph.edges)


@pytest.mark.asyncio
async def test_full_category_api_keeps_isolated_seeds_and_bypasses_display_budgets():
    app = FastAPI()
    app.include_router(api.router)
    store = CategoryFixtureStore()

    async def session():
        yield None

    async def query(session, callback):
        return callback(None, GraphQueries(store))

    app.dependency_overrides[api.get_session] = session
    with patch.object(api, "_query", query):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get("/video-graph/graph", params={
                "entity_type": "product", "full": "true", "max_nodes": 2,
                "max_edges": 1, "min_component_size": 0,
            })
    assert response.status_code == 200
    graph = response.json()
    assert len(graph["nodes"]) == 607
    assert len(graph["edges"]) == 2
    assert not graph["truncated"]


@pytest.mark.asyncio
async def test_full_mode_keeps_only_main_component_without_reintroducing_display_budgets():
    app = FastAPI()
    app.include_router(api.router)
    store = GraphFixtureStore()
    smaller = [dict(id=str(uuid.uuid4()), canonical_name=f"Small {i}", entity_type="technology")
               for i in range(20)]
    store.entities.extend(smaller)
    store.rows.extend(dict(r=dict(id=str(uuid.uuid4()), predicate="uses"),
                           s=smaller[i], o=smaller[i+1], count=2,
                           asserted=1, uncertain=1, negated=0) for i in range(19))

    async def session():
        yield None

    async def query(session, callback):
        return callback(None, GraphQueries(store))

    app.dependency_overrides[api.get_session] = session
    with patch.object(api, "_query", query):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get("/video-graph/graph", params={
                "full": "true", "entity_type": "technology", "min_component_size": 5,
                "max_nodes": 2, "max_edges": 1,
            })
    assert response.status_code == 200
    graph = response.json()
    assert len(graph["nodes"]) == 420  # Hide ten isolated entities and a 20-node component.
    assert len(graph["edges"]) == 419


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,expected,status",
    [
        ({"full": "true", "max_nodes": 2, "max_edges": 1}, (None, None), 200),
        ({"full": "true", "min_component_size": 5}, (None, None), 200),
        ({"full": "true", "min_component_size": 8}, (None, None), 200),
        ({"full": "true", "min_component_size": 0}, (None, None), 200),
        ({"full": "false", "max_nodes": 2, "max_edges": 1}, (2, 1), 200),
        ({}, (300, 500), 200),
        ({"max_nodes": 500, "max_edges": 1000}, (500, 1000), 200),
        ({"max_nodes": 501}, None, 422),
        ({"max_edges": 1001}, None, 422),
        ({"min_component_size": -1}, None, 422),
        ({"min_component_size": 51}, None, 422),
        ({"min_component_size": 0}, (300, 500), 200),
        ({"min_component_size": 8}, (300, 500), 200),
    ],
)
async def test_api_limits_and_full_mode_preserve_filters(params, expected, status):
    app = FastAPI()
    app.include_router(api.router)

    async def session():
        yield None

    app.dependency_overrides[api.get_session] = session
    captured = {}

    class Service:
        @staticmethod
        def get_graph(session, **kwargs):
            captured.update(kwargs)
            return GraphData(nodes=[], edges=[])

    async def query(session, callback):
        return callback(None, Service())

    with patch.object(api, "_query", query):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/video-graph/graph",
                params={
                    **params,
                    "q": "PX4",
                    "entity_type": "software",
                    "assertion": "uncertain",
                    "hops": 2,
                },
            )
    assert response.status_code == status
    if status != 200:
        assert not captured
        return
    assert (captured["max_nodes"], captured["max_edges"]) == expected
    assert captured["q"] == "PX4"
    assert captured["entity_type"] == "software"
    assert captured["assertion"] == "uncertain"
    assert captured["hops"] == 2
    assert captured["min_component_size"] == (
        params.get("min_component_size", 0 if params.get("full") == "true" else 5)
    )
