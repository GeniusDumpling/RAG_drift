"""Search billing fallback, task termination, and full-size supplier evidence retrieval."""

import json

import baidu_search
import pipeline
import pytest
import requests
import supplier_config
import supplier_verify


@pytest.fixture(autouse=True)
def reset_supplier_context():
    supplier_config.configure()
    yield
    supplier_config.configure()
    baidu_search.set_run_id(None)


def response(status, payload, url):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = json.dumps(payload).encode()
    return result


def references(count):
    return [{"url": f"https://source.test/{i}", "title": str(i)} for i in range(count)]


@pytest.mark.parametrize("provider", ["auto", "exa"])
@pytest.mark.parametrize("baidu_status", [200, 402])
def test_exa_402_falls_back_and_baidu_402_is_terminal(monkeypatch, provider, baidu_status):
    supplier_config.configure("parrot")
    monkeypatch.setenv("EXA_API_KEY", "test-exa-key")
    monkeypatch.setattr(baidu_search, "get_api_key", lambda: "test-baidu-key")
    calls = []

    def post(url, **kwargs):
        if "api.exa.ai" in url:
            calls.append("exa")
            assert kwargs["json"]["numResults"] == 20
            return response(402, {}, url)
        calls.append("baidu")
        payload = json.loads(kwargs["data"])
        assert {"type": "web", "top_k": 50} in payload["resource_type_filter"]
        return response(baidu_status, {"references": references(50)}, url)

    monkeypatch.setattr(baidu_search.requests, "post", post)
    conf = {**supplier_config.load_config()["search"], "provider": provider}
    if baidu_status == 402:
        with pytest.raises(baidu_search.SearchPaymentRequired, match="Baidu search HTTP 402"):
            baidu_search.search("Parrot suppliers", conf)
    else:
        result = baidu_search.search("Parrot suppliers", conf)
        assert len(result["references"]) == 50
        assert all(ref["source"] == "baidu" for ref in result["references"])
    assert calls == ["exa", "baidu"]


def test_double_402_stops_collection_before_later_queries_or_analysis(tmp_path, monkeypatch):
    supplier_config.configure("parrot")
    monkeypatch.setattr(supplier_config, "index_dir", lambda: tmp_path / "_index")
    monkeypatch.setattr(pipeline, "base_dir", lambda: str(tmp_path))
    monkeypatch.setattr(pipeline, "setup_logging", lambda _: "test.log")
    (tmp_path / "logs").mkdir()
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setattr(baidu_search, "get_api_key", lambda: "test-key")
    calls = []

    def blocked(url, **kwargs):
        calls.append(url)
        return response(402, {}, url)

    def unexpected(*args, **kwargs):
        pytest.fail("Analysis and URL completion must not run after search payment failure")

    monkeypatch.setattr(baidu_search.requests, "post", blocked)
    monkeypatch.setattr(pipeline, "llm_main", unexpected)
    monkeypatch.setattr(pipeline, "record_known_urls", unexpected)
    assert pipeline.main() == 1
    assert len(calls) == 2  # One Exa request, one Baidu request, no remaining queries/retries.
    saved = json.loads(next((tmp_path / "logs").glob("*.json")).read_text())
    assert saved["status"] == "failed"


@pytest.mark.parametrize("brand", ["dji", "parrot", "skydio"])
@pytest.mark.parametrize("provider,count", [("exa", 20), ("baidu", 50)])
def test_default_collection_keeps_all_query_results_and_seeds(monkeypatch, brand, provider, count):
    supplier_config.configure(brand)
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setattr(baidu_search, "get_api_key", lambda: "test-key")
    calls = []

    def returned_refs():
        calls.append(provider)
        return [{**ref, "url": ref["url"] + f"/{len(calls)}"} for ref in references(count)]

    def post(url, **kwargs):
        if "api.exa.ai" in url:
            assert kwargs["json"]["numResults"] == 20
            return response(200, {"results": returned_refs()}, url)
        payload = json.loads(kwargs["data"])
        assert {"type": "web", "top_k": 50} in payload["resource_type_filter"]
        return response(200, {"references": returned_refs()}, url)

    monkeypatch.setattr(baidu_search.requests, "post", post)
    conf = {**supplier_config.load_config()["search"], "provider": provider}
    result = baidu_search.collect_references(conf)
    queries = conf.get("queries") or [conf["keyword"]]
    assert len(calls) == len(queries)
    assert len(result) == len(queries) * count + len(conf.get("seed_urls", []))


@pytest.mark.parametrize("brand", ["dji", "parrot", "skydio"])
@pytest.mark.parametrize("count", [20, 50])
def test_default_verifier_fetches_all_returned_results_and_original_sources(
    tmp_path, monkeypatch, brand, count
):
    supplier_config.configure(brand)
    monkeypatch.setattr(supplier_config, "index_dir", lambda: tmp_path)
    monkeypatch.setattr(supplier_verify, "search", lambda *a: {"references": references(count)})
    fetched = []

    def fetch(url, **kwargs):
        fetched.append(url)
        return "Source text"

    monkeypatch.setattr(supplier_verify, "fetch_fulltext", fetch)
    monkeypatch.setattr(
        supplier_verify,
        "call_llm",
        lambda *a: json.dumps({"verdict": "待确认", "confidence": "疑似", "evidence": []}),
    )
    conf = supplier_config.load_config()
    result = supplier_verify.verify_supplier(
        "test-key",
        {"supplier": "Example", "sources": ["https://original.test", "https://source.test/0"]},
        conf["search"],
        {},
        {**conf["verify"], "fetch_delay": 0},
    )
    assert result["searched"] == count
    assert result["fetched"] == count + 1
    assert len(set(fetched)) == count + 1


def test_verification_402_saves_partial_results_and_stops_remaining_suppliers(
    tmp_path, monkeypatch
):
    supplier_config.configure("parrot")
    monkeypatch.setattr(supplier_config, "index_dir", lambda: tmp_path)
    path = tmp_path / "confirmed_relations.md"
    path.write_text("test")
    monkeypatch.setattr(supplier_verify, "load_confirmed_relations", lambda _: {"test": {}})
    monkeypatch.setattr(supplier_verify, "get_llm_config", lambda _: {"api_key": "test-key"})
    monkeypatch.setattr(
        supplier_verify.db_ingest,
        "load_supplier_relations_for_verification",
        lambda *a, **kw: [{"supplier": str(i)} for i in range(5)],
    )
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setattr(baidu_search, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(supplier_verify, "fetch_fulltext", lambda *a, **kw: "Source text")
    monkeypatch.setattr(supplier_verify.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        supplier_verify,
        "call_llm",
        lambda *a: json.dumps({"verdict": "待确认", "confidence": "疑似", "evidence": []}),
    )
    calls, saved = [], []

    def post(url, **kwargs):
        if "api.exa.ai" in url:
            calls.append("exa")
            return response(200 if len(calls) == 1 else 402, {"results": references(20)}, url)
        calls.append("baidu")
        return response(402, {}, url)

    monkeypatch.setattr(baidu_search.requests, "post", post)
    monkeypatch.setattr(supplier_verify.db_ingest, "ingest_supplier_verifications", saved.extend)
    monkeypatch.setattr(
        supplier_verify.db_ingest, "sync_confirmed_relation_statuses", lambda *a, **kw: 0
    )
    with pytest.raises(baidu_search.SearchPaymentRequired):
        supplier_verify.main(str(path))
    assert calls == ["exa", "exa", "baidu"]
    assert [r["supplier"] for r in saved] == ["0", "1"]
    assert saved[0]["verdict"] == "待确认"
    assert "Baidu search HTTP 402" in saved[1]["verdict"]
    report = json.loads(next(tmp_path.glob("verification_*.json")).read_text())
    assert report["status"] == "failed"
    assert report["results"] == saved
    assert len(list((tmp_path / "verification_runs").glob("*.json"))) == 2


def test_default_verification_processes_more_than_three_pending_suppliers(tmp_path, monkeypatch):
    supplier_config.configure("parrot")
    monkeypatch.setattr(supplier_config, "index_dir", lambda: tmp_path)
    path = tmp_path / "confirmed_relations.md"
    path.write_text("test")
    monkeypatch.setattr(supplier_verify, "load_confirmed_relations", lambda _: {"test": {}})
    monkeypatch.setattr(supplier_verify, "get_llm_config", lambda _: {"api_key": "test-key"})
    monkeypatch.setattr(
        supplier_verify.db_ingest,
        "load_supplier_relations_for_verification",
        lambda *a, **kw: [{"supplier": str(i)} for i in range(5)],
    )
    monkeypatch.setattr(supplier_verify, "search", lambda *a: {"references": []})
    saved = []
    monkeypatch.setattr(supplier_verify.db_ingest, "ingest_supplier_verifications", saved.extend)
    monkeypatch.setattr(
        supplier_verify.db_ingest, "sync_confirmed_relation_statuses", lambda *a, **kw: 0
    )
    supplier_verify.main(str(path))
    assert [r["supplier"] for r in saved] == [str(i) for i in range(5)]
