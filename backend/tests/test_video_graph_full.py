"""Full graph browsing must not silently retain the old node/edge caps."""

import uuid
from unittest.mock import patch

import httpx
import pytest
from app.api import video_graph as api
from app.schemas.video_graph import GraphData
from app.services.neo4j_graph.queries import GraphQueries
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,expected,status",
    [
        ({"full": "true", "max_nodes": 2, "max_edges": 1}, (None, None), 200),
        ({"full": "false", "max_nodes": 2, "max_edges": 1}, (2, 1), 200),
        ({}, (300, 500), 200),
        ({"max_nodes": 500, "max_edges": 1000}, (500, 1000), 200),
        ({"max_nodes": 501}, None, 422),
        ({"max_edges": 1001}, None, 422),
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
