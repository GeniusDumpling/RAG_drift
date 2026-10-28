"""Brand isolation, document structure, grounded evidence and search transport regressions."""

import json
from pathlib import Path

import baidu_search
import documents
import llm_supplier_analysis as analysis
import pymupdf
import pytest
import supplier_config
import supplier_verify
import yaml
from bs4 import BeautifulSoup


@pytest.fixture(autouse=True)
def reset_brand():
    supplier_config.configure()
    yield
    supplier_config.configure()


def profile(tmp_path, brand="parrot"):
    conf = supplier_config.load_config()
    conf["brand"] = brand
    conf_path = tmp_path / "conf.yaml"
    conf_path.write_text(yaml.safe_dump(conf, allow_unicode=True))
    supplier_config.configure(config_path=conf_path)


def test_brand_config_isolation_and_default():
    assert supplier_config.brand()["name"] == "大疆"
    old = supplier_config.index_dir()
    supplier_config.configure("parrot")
    assert supplier_config.brand()["name"] == "Parrot"
    assert "大疆" not in baidu_search.load_conf()["search"]["instruction"]
    assert supplier_config.index_dir() != old
    assert supplier_config.matches_buyer("Parrot Drones SAS")
    assert not supplier_config.matches_buyer("Parrot reseller")
    supplier_config.configure("skydio")
    assert not supplier_config.matches_buyer("Parrot")
    assert supplier_config.matches_buyer("SKYDIO, INC.")


def test_known_urls_are_independent_per_brand(tmp_path, monkeypatch):
    monkeypatch.setattr(supplier_config, "ROOT", tmp_path)
    supplier_config.configure(
        config_path=Path(__file__).parents[2] / "tools/supplier-information/conf.yaml"
    )
    baidu_search.record_known_urls(["https://same.example/source"])
    supplier_config.configure(
        "parrot", Path(__file__).parents[2] / "tools/supplier-information/conf.yaml"
    )
    assert not baidu_search.load_known_urls()
    baidu_search.record_known_urls(["https://same.example/source"])
    assert baidu_search.load_known_urls() == {"https://same.example/source"}


def pdf_bytes():
    doc = pymupdf.open()
    page = doc.new_page()
    for y in (80, 110, 140):
        page.draw_line((50, y), (430, y))
    for x in (50, 200, 430):
        page.draw_line((x, 80), (x, 140))
    for x, y, text in [
        (60, 100, "Supplier"),
        (210, 100, "Component"),
        (60, 130, "Nidec"),
        (210, 130, "X10 propulsion motor"),
    ]:
        page.insert_text((x, y), text)
    doc.new_page().insert_text((50, 80), "Skydio X10 uses Nidec motors. Published 2024.")
    result = doc.tobytes()
    doc.close()
    return result


def test_pdf_text_tables_page_numbers_and_unknown_date():
    parsed = documents.parse_pdf(pdf_bytes(), "https://example/source")
    assert len(parsed["pages"]) == 2
    assert parsed["table_count"] == 1
    assert "| Supplier | Component |" in parsed["text"]
    assert "| Nidec | X10 propulsion motor |" in parsed["text"]
    assert "### PDF page 2" in parsed["text"]
    assert parsed["published_at"] == ""


def test_extensionless_pdf_is_detected_by_magic_bytes(tmp_path, monkeypatch):
    class Response:
        headers = {"Content-Type": "application/octet-stream"}
        url = "https://example/download?id=123"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield pdf_bytes()

    monkeypatch.setattr(documents.requests, "get", lambda *a, **k: Response())
    result = documents.fetch_document("https://example/download?id=123", cache_dir=tmp_path)
    assert result["format"] == "pdf"
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["pages"][0]["page"] == 1


def test_html_table_preserves_rowspan_and_cell_links():
    soup = BeautifulSoup(
        "<table><tr><th>Model</th><th>Supplier</th></tr>"
        '<tr><td rowspan="2">X10</td><td>Nidec</td></tr>'
        "<tr><td>FLIR</td></tr></table>",
        "html.parser",
    )
    rows = documents.html_table_rows(soup.table)
    assert rows == [["Model", "Supplier"], ["X10", "Nidec"], ["X10", "FLIR"]]
    assert "| X10 | FLIR |" in documents.markdown_table(rows)


def test_brave_english_query_and_normalized_result(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "web": {
                    "results": [
                        {
                            "title": "Nidec",
                            "url": "https://nidec.test/news",
                            "description": "Skydio X10",
                            "extra_snippets": ["motors"],
                            "page_age": "2024-07-22",
                        }
                    ]
                }
            }

    def get(url, **kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(baidu_search.requests, "get", get)
    result = baidu_search.search("Skydio suppliers", {"provider": "brave", "language": "en"})
    assert seen["params"]["search_lang"] == "en"
    assert seen["headers"]["X-Subscription-Token"] == "test-key"
    assert result["references"][0]["date"] == "2024-07-22"
    assert result["references"][0]["source"] == "brave"


def test_grounding_unknown_year_and_cross_brand_filter(tmp_path):
    profile(tmp_path)
    path = tmp_path / "summary.md"
    path.write_text("""### 关系1
- 采购方：Parrot
- 供应商：Micron
- 供应内容/模块：LPDDR5
- 机型：ANAFI UKR
- 证据年份：未知
- 关系类型：器件采用
- 可信度：明确
- 依据（引用原文）：DDR: Micron LPDDR5
- 来源（网页原始URL）：https://parrot.test/whitepaper
### 关系2
- 采购方：Skydio
- 供应商：Micron
- 供应内容/模块：memory
- 可信度：明确
- 依据（引用原文）：not present
- 来源（网页原始URL）：https://invented.test/
""")
    rels = analysis.parse_relations(str(path))
    grounded = analysis.grounded_relations(
        rels,
        [
            {
                "url": "https://parrot.test/whitepaper",
                "body": "ANAFI UKR Main processor\nDDR: Micron LPDDR5\n",
            }
        ],
    )
    assert len(grounded) == 1
    assert grounded[0]["models"] == ["ANAFI UKR"]
    assert grounded[0]["evidence_years"] == []
    assert grounded[0]["relation_type"] == "器件采用"


def test_long_documents_are_bounded_and_keep_all_source_text():
    body = "\n".join(f"page {i} content " + "x" * 150 for i in range(100))
    items = [{"title": "whitepaper", "url": "https://p.test/pdf", "date": "", "body": body}]
    chunks = analysis.split_documents(items, max_chars=4000)
    assert len(chunks) > 1
    assert "".join(item["body"] for chunk in chunks for item in chunk) == body
    assert all(sum(len(item["body"]) + 1000 for item in chunk) <= 4000 for chunk in chunks)
    assert all(item["url"] == "https://p.test/pdf" for chunk in chunks for item in chunk)


def test_verifier_rejects_invented_confirmation(monkeypatch):
    supplier_config.configure("skydio")
    monkeypatch.setattr(supplier_verify, "search", lambda *a: {"references": []})
    monkeypatch.setattr(
        supplier_verify, "fetch_fulltext", lambda *a, **k: "No relationship stated."
    )
    monkeypatch.setattr(
        supplier_verify,
        "call_llm",
        lambda *a: """- 验证结论：确认
- 可信度：明确
- 依据（引用原文）：Nidec supplies Skydio.
- 来源URL：https://nidec.test/news
""",
    )
    with pytest.raises(ValueError, match="verbatim quote"):
        supplier_verify.verify_supplier(
            "key",
            {"supplier": "Nidec", "sources": ["https://nidec.test/news"]},
            {},
            {},
            {"fetch_limit": 1, "fetch_delay": 0},
        )


def test_missing_metadata_is_not_inferred_from_current_date_or_copyright():
    models, years = analysis.ground_metadata(
        ["X10", "X2"],
        [2024, 2025, 2026],
        "Skydio X10 uses this motor.\n© 2026 Nidec. All rights reserved.",
        "2024-07-22",
    )
    assert models == ["X10"]
    assert years == [2024]


def test_auto_search_falls_back_but_explicit_brave_preserves_error(monkeypatch):
    import requests

    response = requests.Response()
    response.status_code = 422

    def invalid(*args):
        raise requests.HTTPError("invalid token", response=response)

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "invalid-test-key")
    monkeypatch.setattr(baidu_search, "search_brave", invalid)
    monkeypatch.setattr(baidu_search, "get_api_key", lambda: "test-baidu-key")
    monkeypatch.setattr(
        baidu_search,
        "search_baidu",
        lambda *args: {"references": [{"url": "https://source.test", "title": "source"}]},
    )
    refs = baidu_search.search("Parrot components", {"provider": "auto"})["references"]
    assert refs[0]["source"] == "baidu"
    with pytest.raises(requests.HTTPError):
        baidu_search.search("Parrot components", {"provider": "brave"})


def test_brand_confirmed_index_roundtrip_filters_other_buyer(tmp_path, monkeypatch):
    profile(tmp_path)
    monkeypatch.setattr(supplier_config, "index_dir", lambda: tmp_path / "parrot")
    rel = {
        "supplier": "Shared",
        "buyer": "Parrot",
        "credibility": "明确",
        "url": "https://source.test",
        "supply": "memory",
        "models": ["ANAFI UKR"],
        "evidence_years": [2025],
        "quote": "Parrot uses Shared memory.",
        "relation_type": "器件采用",
    }
    other = {**rel, "supplier": "Wrong brand supplier", "buyer": "Skydio"}
    path = analysis.update_confirmed_relations("", "run1", [rel, other])
    saved = analysis.load_confirmed_relations(path)
    assert set(saved) == {"Shared"}
    assert saved["Shared"]["models"] == ["ANAFI UKR"]
    assert saved["Shared"]["evidence_years"] == [2025]
    assert saved["Shared"]["evidence_items"][0]["url"] == "https://source.test"


def test_pipeline_does_not_mark_failed_analysis_urls_as_processed(tmp_path, monkeypatch):
    import pipeline

    fulltext = tmp_path / "test_fulltext.md"
    fulltext.write_text("## 1. Source\n地址：https://source.test\n\nReal body\n")
    monkeypatch.setattr(pipeline, "setup_logging", lambda _: str(tmp_path / "log"))
    monkeypatch.setattr(pipeline, "base_dir", lambda: str(tmp_path))
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(pipeline, "search_main", lambda _: str(fulltext))

    def failed_analysis(*args):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(pipeline, "llm_main", failed_analysis)
    marked = []
    monkeypatch.setattr(pipeline, "record_known_urls", lambda urls: marked.extend(urls))
    assert pipeline.main() == 1
    assert not marked


def test_empty_truncated_completion_retries_with_larger_budget(monkeypatch):
    import llm_config

    budgets = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            if len(budgets) == 1:
                return {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
            return {"choices": [{"finish_reason": "stop", "message": {"content": "完成"}}]}

    def post(*args, **kwargs):
        budgets.append(kwargs["json"]["max_tokens"])
        return Response()

    monkeypatch.setattr(llm_config.requests, "post", post)
    conf = {
        "api_base": "https://example.test",
        "model": "test",
        "max_tokens": 4096,
        "max_retries": 2,
    }
    assert llm_config.chat_completion("key", conf, "system", "user") == "完成"
    assert budgets == [4096, 8192]


def test_empty_completion_never_becomes_success(monkeypatch):
    import llm_config

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}

    monkeypatch.setattr(llm_config.requests, "post", lambda *a, **kw: Response())
    with pytest.raises(RuntimeError, match="Empty/truncated"):
        llm_config.chat_completion(
            "key",
            {"api_base": "https://example.test", "model": "test", "max_retries": 2},
            "system",
            "user",
        )


def test_http_error_pages_are_not_source_evidence():
    assert not baidu_search.is_usable_fulltext("404 Page Not Found")
    assert not baidu_search.is_usable_fulltext("403 Forbidden: Access denied")


def test_pdf_publication_date_is_carried_into_every_analysis_segment(tmp_path):
    body = "### PDF page 1\nUpdated: July 31st, 2024\n" + ("long body\n" * 3000)
    path = tmp_path / "sample_fulltext.md"
    path.write_text("## 1. ANAFI USA\n地址：https://parrot.test/paper.pdf\n\n" + body)
    items = analysis.parse_fulltext(str(path))
    assert items[0]["date"] == "Updated: July 31st, 2024"
    chunks = analysis.split_documents(items, max_chars=4000)
    assert all(item["date"] == "Updated: July 31st, 2024" for chunk in chunks for item in chunk)
    assert analysis.ground_metadata([], [], "component list", items[0]["date"])[1] == [2024]


def test_pdf_page_citation_and_typographic_quotes_keep_verbatim_meaning():
    assert analysis.source_url("`https://parrot.test/paper.pdf#page=22`") == (
        "https://parrot.test/paper.pdf"
    )
    assert analysis.normalized_quote("ANAFI USA’s `H22` processor") == (
        analysis.normalized_quote("ANAFI USA's H22 processor")
    )
    assert analysis.normalized_quote("supplier A") != analysis.normalized_quote("supplier B")


def test_replayed_metadata_does_not_duplicate_evidence_or_increase_seen_count(
    tmp_path, monkeypatch
):
    profile(tmp_path)
    monkeypatch.setattr(supplier_config, "index_dir", lambda: tmp_path / "parrot")
    rel = {
        "supplier": "Shared",
        "buyer": "Parrot",
        "credibility": "明确",
        "url": "https://source.test",
        "supply": "memory",
        "models": ["ANAFI USA"],
        "evidence_years": [],
        "quote": "Shared memory",
        "relation_type": "器件采用",
    }
    path = analysis.update_confirmed_relations("", "same-run", [rel])
    before = analysis.load_confirmed_relations(path)["Shared"]["count"]
    analysis.update_confirmed_relations("", "same-run", [{**rel, "evidence_years": [2024]}])
    after = analysis.load_confirmed_relations(path)["Shared"]
    assert after["count"] == before
    assert len(after["evidence_items"]) == 1
    assert after["evidence_items"][0]["evidence_years"] == [2024]
