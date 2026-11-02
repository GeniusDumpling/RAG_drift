"""Regressions for actual failed model output and preservation of verification diagnostics."""

import json
from pathlib import Path

import pytest
import requests
import supplier_config
import supplier_verify
from llm_supplier_analysis import source_url
from verification_evidence import match_evidence, parse_response, source_urls

DOCS = [
    {
        "url": "https://a.test/paper.pdf",
        "body": "Skydio X10 uses FLIR Boson+. Published 2023.",
        "date": "",
    },
    {
        "url": "https://b.test/news",
        "body": "FLIR supplies thermal camera modules to Skydio.",
        "date": "",
    },
]


@pytest.fixture(autouse=True)
def isolated_brand(monkeypatch, tmp_path):
    supplier_config.configure("skydio")
    monkeypatch.setattr(supplier_config, "index_dir", lambda: tmp_path)
    yield
    supplier_config.configure()


def test_adjacent_urls_markdown_and_page_fragments():
    value = "[paper](https://a.test/paper.pdf#page=22)；https://b.test/news。"
    assert source_urls(value) == ["https://a.test/paper.pdf", "https://b.test/news"]
    assert source_url(value) == "https://a.test/paper.pdf"
    assert source_urls("https://a.test/paper.pdf;https://b.test/news") == [
        "https://a.test/paper.pdf",
        "https://b.test/news",
    ]


def test_legacy_bold_ascii_colons_multiline_and_separate_source_blocks():
    parsed = parse_response("""- **验证结论**: 确认
- **可信度：** 明确
- 依据（引用原文）：
  > Skydio X10 uses FLIR Boson+.
- 来源URL：https://a.test/paper.pdf
- 依据（引用原文）：
  “FLIR supplies thermal camera modules to Skydio.”
- 来源URL：https://b.test/news
""")
    assert parsed["verdict"] == "确认"
    assert parsed["confidence"] == "明确"
    # Markdown blockquote markers are formatting, not part of the original sentence.
    assert len(match_evidence(parsed["candidates"], DOCS)) == 2


def test_legacy_multi_source_quote_prose_is_grounded_per_original_document():
    parsed = parse_response("""- 验证结论：确认
- 可信度：明确
- 依据（引用原文）：网页1、网页2分别载明：
“Skydio X10 uses FLIR Boson+.” “FLIR supplies thermal camera modules to Skydio.”
- 来源URL：https://a.test/paper.pdf；https://b.test/news
""")
    assert [e["url"] for e in match_evidence(parsed["candidates"], DOCS)] == [
        d["url"] for d in DOCS
    ]


def test_structured_sources_cannot_be_swapped_or_launder_fabricated_quotes():
    evidence = [
        {"url": DOCS[0]["url"], "quote": "Skydio X10 uses FLIR Boson+."},
        {"url": DOCS[1]["url"], "quote": "Skydio X10 uses FLIR Boson+."},
    ]
    parsed = parse_response(json.dumps({"evidence": evidence}))
    with pytest.raises(ValueError, match="verbatim quote"):
        match_evidence(parsed["candidates"], DOCS)
    evidence[1]["url"] = DOCS[0]["url"] + "；" + DOCS[1]["url"]
    with pytest.raises(ValueError, match="exactly one"):
        match_evidence(parse_response(json.dumps({"evidence": evidence}))["candidates"], DOCS)


def test_missing_or_fabricated_legacy_quotes_are_rejected():
    for quote in ["", "网页1说：“Skydio X10 uses FLIR Boson+.” “Invented supply agreement.”"]:
        with pytest.raises(ValueError):
            match_evidence([{"urls": [DOCS[0]["url"]], "quote": quote, "strict": False}], DOCS)


def setup_verifier(monkeypatch, raw):
    monkeypatch.setattr(supplier_verify, "search", lambda *a: {"references": []})
    monkeypatch.setattr(
        supplier_verify,
        "fetch_fulltext",
        lambda url, **kw: next(d["body"] for d in DOCS if d["url"] == url),
    )
    monkeypatch.setattr(supplier_verify, "call_llm", lambda *a: raw)


def test_structured_multi_source_success_and_grounded_metadata(monkeypatch):
    raw = json.dumps(
        {
            "verdict": "确认",
            "confidence": "明确",
            "supply": "thermal camera",
            "models": ["X10", "invented model"],
            "evidence_years": [2023, 2025],
            "evidence": [{"url": d["url"], "quote": d["body"]} for d in DOCS],
        }
    )
    setup_verifier(monkeypatch, raw)
    result = supplier_verify.verify_supplier(
        "secret-key",
        {"supplier": "FLIR", "sources": [d["url"] for d in DOCS]},
        {},
        {},
        {"fetch_limit": 2, "fetch_delay": 0},
    )
    assert result["models"] == ["X10"]
    assert result["evidence_years"] == [2023]
    assert len(result["evidence_items"]) == 2
    assert result["raw_response"] == raw
    saved = Path(result["diagnostic_path"]).read_text()
    assert "secret-key" not in saved
    documents = json.loads(saved)["source_documents"]
    assert [(d["url"], d["body"]) for d in documents] == [(d["url"], d["body"]) for d in DOCS]


def test_parse_failure_preserves_raw_response_source_text_and_counters(monkeypatch):
    raw = '```json\n{"verdict": malformed}\n```'
    setup_verifier(monkeypatch, raw)
    with pytest.raises(supplier_verify.VerificationFailure) as error:
        supplier_verify.verify_supplier(
            "secret-key",
            {"supplier": "FLIR", "sources": [DOCS[0]["url"]]},
            {},
            {},
            {"fetch_limit": 1, "fetch_delay": 0},
        )
    result = error.value.result
    saved = json.loads(Path(result["diagnostic_path"]).read_text())
    assert saved["raw_response"] == raw
    assert saved["fetched"] == 1
    assert saved["source_documents"][0]["body"] == DOCS[0]["body"]
    assert raw in result["evidence"]
    assert "secret-key" not in str(saved)


def test_network_failure_retains_diagnostic_error(monkeypatch):
    def offline(*args):
        raise requests.exceptions.SSLError("connection closed")

    monkeypatch.setattr(supplier_verify, "search", offline)
    with pytest.raises(supplier_verify.VerificationFailure) as error:
        supplier_verify.verify_supplier("key", {"supplier": "FLIR"}, {}, {}, {})
    saved = json.loads(Path(error.value.result["diagnostic_path"]).read_text())
    assert saved["raw_response"] == ""
    assert saved["error"] == "connection closed"


def test_truncated_completion_payload_is_saved_before_failure(monkeypatch):
    import llm_config

    payload = {"choices": [{"finish_reason": "length", "message": {"content": '{"partial":'}}]}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    setup_verifier(monkeypatch, "")
    monkeypatch.setattr(llm_config.requests, "post", lambda *a, **kw: Response())
    monkeypatch.setattr(
        supplier_verify,
        "call_llm",
        lambda key, conf, content: llm_config.chat_completion(key, conf, "prompt", content),
    )
    with pytest.raises(supplier_verify.VerificationFailure) as error:
        supplier_verify.verify_supplier(
            "secret-key",
            {"supplier": "FLIR", "sources": [DOCS[0]["url"]]},
            {},
            {"api_base": "https://llm.test", "model": "test", "max_retries": 1},
            {"fetch_limit": 1, "fetch_delay": 0},
        )
    saved = json.loads(Path(error.value.result["diagnostic_path"]).read_text())
    assert saved["response_attempts"] == [payload]
    assert saved["raw_response"] == '{"partial":'
    assert "Empty/truncated" in saved["error"]
