"""Behavioral checks for indexed recall, overlap and lossless context reuse."""
import asyncio
import threading

import pytest
from app.schemas.search import SearchFilters
from app.services import retrieval
from app.services.query_matching import term_weights
from app.services.retrieval import _keyword_seed_terms

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize('terms', [
    ['mavic 3 pro', 'dji', 'mavic', '3', 'pro', '主板芯片'],
    ['px4', '外部定位'], ['rtk'], ['ai', '3'], ['gps', 'pro', '3'],
])
def test_seed_pruning_cannot_drop_an_acceptable_lexical_match(terms):
    # Exhaust every match subset, assuming maximum per-field contributions.
    seeds = set(_keyword_seed_terms(terms))
    weights = term_weights(terms)
    for mask in range(1 << len(terms)):
        matched = [i for i in range(len(terms)) if mask & (1 << i)]
        score = sum(weights[i] for i in matched) / max(sum(weights), 1.0)
        if score >= retrieval._KEYWORD_MINIMUM_MATCH_SCORE:
            assert any(terms[i] in seeds for i in matched)
    assert _keyword_seed_terms(terms, entity_terms=['dji']) == terms


async def test_vector_and_keyword_branches_really_overlap(monkeypatch):
    vector_started, sql_started = threading.Event(), threading.Event()

    def vector(self, **kwargs):
        vector_started.set()
        assert sql_started.wait(2), 'SQL did not start while vector search was pending'
        return []

    async def keyword(*args, **kwargs):
        sql_started.set()
        assert await asyncio.to_thread(vector_started.wait, 2)
        return []

    monkeypatch.setattr(retrieval.QdrantIndexer, 'search_chunks', vector)
    monkeypatch.setattr(retrieval, '_keyword_search', keyword)
    result = await retrieval.retrieve_evidence(
        object(), raw_query='PX4', optimized_query_text='PX4', keyword_terms=['PX4'],
        filters=SearchFilters(), top_k=8, qdrant_url='memory://overlap-test',
        qdrant_collection='test', embedding=object(),
    )
    assert result.evidence == []
    assert result.trace['execution_version'] == 'indexed-parallel-v1'
    assert result.trace['vector']['failed'] is False
    assert result.trace['keyword']['latency_ms'] >= 0
