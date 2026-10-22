from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import requests

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "tools/web-crawler"
SPEC = importlib.util.spec_from_file_location("web_crawler", SCRIPT_DIR / "crawl.py")
assert SPEC and SPEC.loader
crawler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crawler)


def config(tmp_path: Path, **search_options) -> dict:
    path = tmp_path / "conf.yaml"
    path.write_text(
        "search:\n  keywords: [飞控, 无人机]\ncrawl:\n  delay: 0\noutput:\n  directory: results\n",
        encoding="utf-8",
    )
    conf = crawler.load_config(path)
    conf["search"].update(search_options)
    return conf


def response(value, *, url="https://example.com/article", status=200, content_type="text/html"):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result.headers["Content-Type"] = content_type
    result.encoding = "utf-8"
    result._content = (
        json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
    )
    return result


class Session:
    def __init__(self, searches, pages=()):
        self.searches = iter(searches)
        self.pages = iter(pages)
        self.posts = []
        self.gets = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return next(self.searches)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        value = next(self.pages)
        if isinstance(value, Exception):
            raise value
        return value


def read_run(path: Path):
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    docs = [json.loads(line) for line in (path / "documents.jsonl").read_text().splitlines()]
    return manifest, docs


@pytest.mark.parametrize("top_k", [0, 51, True, "10"])
def test_rejects_invalid_result_limits(tmp_path, top_k):
    path = tmp_path / "conf.yaml"
    path.write_text(f"search:\n  keywords: [飞控]\n  top_k: {json.dumps(top_k)}\n")
    with pytest.raises(ValueError, match="top_k"):
        crawler.load_config(path)


def test_payload_limits_only_web_and_resolves_output_relative_to_config(tmp_path):
    conf = config(tmp_path, top_k=7, recency_filter="week", domain_filter=["example.com"])
    payload = crawler.search_payload("飞控", conf["search"])
    assert payload["resource_type_filter"] == [
        {"type": "web", "top_k": 7},
        {"type": "video", "top_k": 0},
        {"type": "image", "top_k": 0},
        {"type": "aladdin", "top_k": 0},
    ]
    assert payload["search_recency_filter"] == "week"
    assert payload["search_filter"] == {"match": {"site": ["example.com"]}}
    assert conf["output"]["directory"] == str(tmp_path / "results")


def test_preserves_evidence_deduplicates_and_extracts_actual_html(tmp_path, monkeypatch):
    conf = config(tmp_path)
    first = {
        "request_id": "r1",
        "references": [
            {
                "title": "搜索标题",
                "url": "https://EXAMPLE.com:443/article#one",
                "content": "搜索摘要",
                "source": "原始来源",
                "date": "2026-10-20",
            }
        ],
    }
    second = {"references": [{"url": "https://example.com/article#two", "content": "另一摘要"}]}
    paragraphs = "".join(
        f"<p>第{i}段介绍无人机飞行控制系统。飞控通过传感器融合与反馈控制实现稳定飞行，"
        "并对飞行状态持续监测。开发者需要结合实际飞行环境验证控制算法的可靠性。</p>"
        for i in range(12)
    )
    html = (
        "<html lang='zh'><head><title>飞控技术正文</title></head><body>"
        f"<article>{paragraphs}</article></body></html>"
    ).encode()
    session = Session([response(first), response(second)], [response(html)])
    monkeypatch.setattr(crawler, "make_session", lambda _retries: session)

    path = crawler.run(conf, "secret-key")
    manifest, docs = read_run(path)

    assert manifest["status"] == "success"
    assert manifest["counts"] == {"success": 1}
    assert len(session.gets) == len(docs) == 1
    doc = docs[0]
    assert len(doc["search_matches"]) == 2
    assert doc["search_matches"][0]["reference_pointer"] == "/references/0"
    assert "传感器融合" in doc["text"]
    assert doc["text"] != "搜索摘要"
    assert doc["text_sha256"] == crawler.digest(doc["text"])
    assert doc["document_id"] == "web:" + crawler.digest("https://example.com/article")
    assert (path / doc["raw_page_path"]).read_bytes() == html
    assert json.loads((path / "raw/search/001.json").read_bytes()) == first
    assert "secret-key" not in (path / "manifest.json").read_text()
    assert "headers" not in session.gets[0][1]


def test_failed_page_has_empty_body_and_does_not_block_other_documents(tmp_path, monkeypatch):
    conf = config(tmp_path)
    conf["search"]["keywords"] = ["飞控"]
    refs = [
        {"url": "https://example.com/fail", "content": "只能作为摘要"},
        {"url": "https://example.com/ok"},
    ]
    session = Session(
        [response({"references": refs})],
        [
            requests.Timeout("timeout"),
            response("真实正文".encode(), content_type="text/plain"),
        ],
    )
    monkeypatch.setattr(crawler, "make_session", lambda _retries: session)

    manifest, docs = read_run(crawler.run(conf, "key"))

    assert manifest["status"] == "partial"
    assert docs[0]["status"] == "failed"
    assert docs[0]["text"] == ""
    assert docs[0]["text_sha256"] is None
    assert docs[0]["search_matches"][0]["snippet"] == "只能作为摘要"
    assert docs[1]["status"] == "success"


def test_search_error_response_is_retained_and_other_keywords_continue(tmp_path, monkeypatch):
    session = Session(
        [
            response({"error": {"code": "Unauthorized"}}, status=401),
            response({"references": []}),
        ]
    )
    monkeypatch.setattr(crawler, "make_session", lambda _retries: session)
    path = crawler.run(config(tmp_path), "key")
    manifest, docs = read_run(path)
    assert docs == []
    assert manifest["status"] == "partial"
    assert [search["status"] for search in manifest["searches"]] == ["failed", "success"]
    assert json.loads((path / "raw/search/001.json").read_bytes())["error"]


def test_limit_and_search_only_never_fetch_and_do_not_populate_text(tmp_path, monkeypatch):
    conf = config(tmp_path, top_k=1)
    conf["crawl"]["enabled"] = False
    conf["search"]["keywords"] = ["飞控"]
    session = Session(
        [
            response(
                {
                    "references": [
                        {"url": "file:///tmp/x"},
                        {"url": "https://example.com/one", "content": "摘要"},
                        {"url": "https://example.com/two"},
                    ]
                }
            )
        ]
    )
    monkeypatch.setattr(crawler, "make_session", lambda _retries: session)
    manifest, docs = read_run(crawler.run(conf, "key"))
    assert len(docs) == 1
    assert docs[0]["status"] == "search_only"
    assert docs[0]["text"] == ""
    assert docs[0]["fetched_at"] is None
    assert not session.gets
    assert manifest["searches"][0]["invalid"] == 1


@pytest.mark.parametrize(
    "content_type,body,status",
    [
        ("application/pdf", b"%PDF", "unsupported"),
        ("text/html", b"<html><body></body></html>", "empty"),
    ],
)
def test_preserves_pages_without_usable_body(tmp_path, content_type, body, status):
    (tmp_path / "raw/pages").mkdir(parents=True)
    session = Session([], [response(body, content_type=content_type)])
    document = crawler.new_document("https://example.com/", {"url": "https://example.com/"}, "run")
    crawler.fetch_document(session, document, tmp_path, {"timeout": 1})
    assert document["status"] == status
    assert document["text"] == ""
    assert (tmp_path / document["raw_page_path"]).read_bytes() == body


def test_dry_run_does_not_require_api_key_or_create_outputs(tmp_path, monkeypatch, capsys):
    config(tmp_path)
    monkeypatch.delenv("BAIDU_QIANFAN_API_KEY", raising=False)
    assert crawler.main(["--config", str(tmp_path / "conf.yaml"), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["requests"]
    assert not (tmp_path / "results").exists()


def test_extractor_failure_keeps_raw_page_for_reprocessing(tmp_path, monkeypatch):
    (tmp_path / "raw/pages").mkdir(parents=True)
    session = Session([], [response(b"<html>broken</html>")])
    document = crawler.new_document("https://example.com/", {"url": "https://example.com/"}, "run")

    def broken_extractor(*_args, **_kwargs):
        raise RuntimeError("parser failed")

    monkeypatch.setattr(crawler.trafilatura, "bare_extraction", broken_extractor)
    crawler.fetch_document(session, document, tmp_path, {"timeout": 1})
    assert document["status"] == "failed"
    assert "parser failed" in document["error"]
    assert (tmp_path / document["raw_page_path"]).exists()


@pytest.mark.parametrize("payload", [{"error": "bad request"}, {"references": None}])
def test_business_error_does_not_look_like_zero_results(tmp_path, monkeypatch, payload):
    conf = config(tmp_path)
    conf["search"]["keywords"] = ["飞控"]
    monkeypatch.setattr(crawler, "make_session", lambda _retries: Session([response(payload)]))
    path = crawler.run(conf, "key")
    manifest, docs = read_run(path)
    assert manifest["status"] == "partial"
    assert manifest["searches"][0]["status"] == "failed"
    assert json.loads((path / "raw/search/001.json").read_bytes()) == payload
    assert docs == []
