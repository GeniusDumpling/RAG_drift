import csv
import json

import fitz
import pytest

from tools.autel_documents.collector import Client, Collector, FetchError, verify
from tools.autel_documents.sources import (
    allowed_url,
    discover,
    extract_specifications,
    guess_language,
    page_scope,
)

ROOT = "https://www.autelrobotics.cn/"
PDF = ROOT + "files/manual.pdf"


def pdf_bytes():
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "Official manual test fixture")
        return document.tobytes()


class FakeClient:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def fetch(self, url, *, document=False):
        self.calls.append(url)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value, url, {"content-type": "text/html; charset=utf-8"}


def page(body):
    return ("<html><head><title>Autel test</title></head><body>" + body + "</body></html>").encode()


def setup_corpus(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.autel_documents.collector.seeds", lambda: [ROOT])
    client = FakeClient(
        {
            ROOT: page(
                '<a href="/doc/evo-max/">EVO Max</a>'
                '<a href="/doc/alpha/">Alpha</a><a href="/doc/missing/">Missing</a>'
            ),
            ROOT + "doc/evo-max/": page('<a href="/files/manual.pdf">Max user manual</a>'),
            ROOT + "doc/alpha/": page('<a href="/files/manual.pdf">Alpha shared manual</a>'),
            ROOT + "doc/missing/": FetchError("HTTP 404", 404),
            PDF: pdf_bytes(),
        }
    )
    collector = Collector(tmp_path, client)
    collector.discover_pages()
    collector.download()
    return collector, client


def test_official_and_cdn_policy():
    assert allowed_url(ROOT + "doc/evo/")
    assert allowed_url("https://app.autelrobotics.cn/files/a.pdf", document=True)
    assert allowed_url("https://cdn.shopify.com/files/a.pdf", document=True)
    assert allowed_url("https://d42ar9bw0cz7m.cloudfront.net/a.pdf", document=True)
    assert not allowed_url("https://unrelated.cloudfront.net/a.pdf", document=True)
    assert not allowed_url("https://cdn.shopify.com/pages/")
    for url in [
        "https://autelrobotics.cn.evil.test/a.pdf",
        "file:///etc/passwd",
        "https://user:pass@www.autelrobotics.cn/",
        "http://127.0.0.1/a.pdf",
    ]:
        assert not allowed_url(url, document=True)


def test_real_download_row_metadata_and_literal_legacy_menu():
    source = """<html><ul><li><h4 class="application-down-lih4">用户手册（高级套装）</h4>
    <div class="application-down-edit">V1.0.2</div>
    <div class="application-down-time">2025-01</div>
    <a href="/files/中文 手册.pdf">PDF</a></li></ul>
    <script>
    var menus_01 = '[{"link":{"title":"EVO II V2","url":"/productdetail/evo-ii-v2/"}}]';
    </script>
    <a href="https://evil.test/manual.pdf">not official</a>
    <a href="/files/firmware.zip">firmware</a>
    <!-- <a href="/files/old.pdf">removed document</a> -->
    </html>"""
    pages, documents = discover(source, ROOT + "doc/evo/")
    assert [r["url"] for r in pages] == [ROOT + "productdetail/evo-ii-v2/"]
    assert len(documents) == 1
    assert documents[0]["title"] == "用户手册（高级套装）"
    assert documents[0]["official_version"] == "V1.0.2"
    assert documents[0]["date"] == "2025-01"
    assert "%E4%B8%AD" in documents[0]["url"] and "%20" in documents[0]["url"]


def test_english_and_shop_metadata_are_preserved():
    source = """<html>
    <div class="tabbox"><h5><b>EN_EVO Max Series User Manual_V1.3.0</b>
    <p>2025-08-01</p></h5><a href="/files/123.pdf">Download</a></div>
    <ul><li class="content-li"><p class="pdf-name">Quick Start Guide</p>
    <p class="pdf-time">Jan-13-2021</p><a href="/files/456.pdf">PDF</a></li></ul>
    </html>"""
    _, docs = discover(source, ROOT)
    assert docs[0]["title"] == "EN_EVO Max Series User Manual_V1.3.0"
    assert docs[0]["official_version"] == "V1.3.0"
    assert docs[0]["date"] == "2025-08-01"
    assert docs[0]["document_type"] == "user_manual"
    assert docs[1]["title"] == "Quick Start Guide" and docs[1]["date"] == "Jan-13-2021"


def test_numeric_page_ids_are_not_models_and_navigation_excludes_sdk_and_void():
    assert page_scope(ROOT + "doc/591", "EVO Max Series Related Downloads") == "evo-max-series"
    assert page_scope(ROOT + "doc/468", "Autel Integrated Command System") == "command-center"
    assert page_scope(ROOT + "doc/195", "AutelSmartController") == "autel-smart-controller"
    source = """<html><a href="void(0)">disabled</a>
      <a href="/productdetail/dragonfish/void(0)">invalid</a>
      <a href="https://developer.autelrobotics.com/doc/v2.6/mobile_sdk/en/00/1">SDK</a>
      <a href="/doc/evo-max/">Manuals</a></html>"""
    pages, _ = discover(source, ROOT + "productdetail/dragonfish/")
    assert [p["url"] for p in pages] == [ROOT + "doc/evo-max/"]


def test_explicit_language_markers_override_chinese_folder_names():
    assert guess_language("User Manual", ROOT + "中文目录/EN_User_Manual.pdf") == "en"
    assert guess_language("EVO 产品清单-法语", ROOT + "123.pdf") == "fr"
    assert guess_language("JP_EVO航空機ユーザーマニュアル", ROOT + "123.pdf") == "ja"
    assert guess_language("多语言快速指引", ROOT + "123.pdf") == "mul"
    assert guess_language("123", ROOT + "123.pdf") == "und"


def test_reindex_uses_only_verified_snapshots_and_preserves_capture_time(tmp_path, monkeypatch):
    collector, client = setup_corpus(tmp_path, monkeypatch)
    before_calls = len(client.calls)
    captured = {u: r.get("collected_at") for u, r in collector.state["pages"].items()}
    doc = next(iter(collector.state["documents"].values()))
    doc["title"] = "obsolete parser output"
    collector.reindex()
    assert len(client.calls) == before_calls
    assert {u: r.get("collected_at") for u, r in collector.state["pages"].items()} == captured
    assert all(
        r["title"] != "obsolete parser output" for r in collector.state["documents"].values()
    )
    good = next(r for r in collector.state["pages"].values() if r["status"] == "saved")
    (tmp_path / good["local_path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Snapshot hash mismatch"):
        collector.reindex()


def test_resume_hash_verification_shared_file_and_explicit_failures(tmp_path, monkeypatch):
    collector, client = setup_corpus(tmp_path, monkeypatch)
    assert client.calls.count(PDF) == 1
    documents = list(collector.state["documents"].values())
    assert len(documents) == 2
    assert len({r["local_path"] for r in documents}) == 1
    assert verify(tmp_path)["ok"]
    before = len(client.calls)
    resumed = Collector(tmp_path, client)
    resumed.discover_pages()
    resumed.download()
    assert client.calls[before:] == [ROOT + "doc/missing/"]
    report = json.loads((tmp_path / "汇总.json").read_text())
    assert report["unique_pdf_contents"] == 1 and report["failed_pages"] == 1
    assert report["pending_documents"] == report["pending_pages"] == 0
    path = tmp_path / documents[0]["local_path"]
    path.write_bytes(b"corrupted file")
    assert not verify(tmp_path)["ok"]
    repaired = Collector(tmp_path, client)
    repaired.download()
    assert client.calls.count(PDF) == 2
    assert verify(tmp_path)["ok"]


def test_pagination_limit_is_pending_work_not_silent_success(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.autel_documents.collector.seeds", lambda: [ROOT])
    client = FakeClient({ROOT: page('<a href="/doc/evo/">EVO</a>')})
    collector = Collector(tmp_path, client)
    collector.discover_pages(max_pages=1)
    report = collector.export()
    assert report["pending_pages"] == 1
    assert collector.state["pending_pages"][0]["url"] == ROOT + "doc/evo/"


def test_specifications_keep_qualifiers_and_quarantine_ambiguous_columns():
    groups, notes = extract_specifications("""<html><div class="insrecruit-list"><ul><li>
      <div class="rectue-top"><span class="name">无人机</span></div>
      <table><tr><td>最大起飞重量</td><td>非欧盟：1999 克<br>欧盟 C2：1890 克</td></tr>
      <tr><td>续航</td><td>42 min（无风、特定电池）</td></tr>
      <tr><td rowspan="2">尺寸</td><td>unresolved merged value</td></tr>
      <tr><td>Multi model</td><td>model A</td><td>model B</td></tr></table>
      </li></ul></div><table><tr><td>Marketing</td><td>unrelated</td></tr></table></html>""")
    assert groups == [
        {
            "name": "无人机",
            "parameters": [
                {"name": "最大起飞重量", "value": "非欧盟：1999 克\n欧盟 C2：1890 克"},
                {"name": "续航", "value": "42 min（无风、特定电池）"},
            ],
        }
    ]
    assert notes


def test_specification_export_is_compatible_with_existing_drone_parser(tmp_path, monkeypatch):
    from app.services.drones import DroneCatalogService

    url = ROOT + "productdetail/evo-max-4t/"
    monkeypatch.setattr("tools.autel_documents.collector.seeds", lambda: [url])
    source = page(
        '<div class="insrecruit-list"><ul><li><div class="rectue-top">'
        '<span class="name">无人机</span></div><table><tr><td>重量</td>'
        "<td>1665 克（含电池、云台和螺旋桨）</td></tr></table></li></ul></div>"
    )
    collector = Collector(tmp_path, FakeClient({url: source}))
    collector.discover_pages()
    service = DroneCatalogService(tmp_path, manufacturer="Autel")
    assert service.catalog().total == 1
    detail = service.detail("evo-max-4t")
    assert detail.parameter_count == 1
    assert detail.groups[0].parameters[0].value == "1665 克（含电池、云台和螺旋桨）"
    assert verify(tmp_path)["ok"]


class FakeResponse:
    def __init__(self, data=b"", status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.data


@pytest.mark.parametrize("body", [b"<html>Error page</html>", b"%PDF-1.7 truncated"])
def test_download_rejects_error_pages_and_truncated_pdf(monkeypatch, body):
    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResponse(body))
    with pytest.raises(FetchError):
        Client(delay=0, retries=0).fetch(PDF, document=True)


def test_redirect_to_unrelated_host_is_not_requested(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return FakeResponse(status=302, headers={"Location": "http://127.0.0.1/private.pdf"})

    monkeypatch.setattr("requests.get", get)
    with pytest.raises(FetchError, match="outside official"):
        Client(delay=0, retries=0).fetch(PDF, document=True)
    assert calls == [PDF]


def test_invalid_pdf_is_recorded_as_failure_not_written(tmp_path):
    client = FakeClient({PDF: FetchError("Response is not a PDF", 200)})
    collector = Collector(tmp_path, client)
    collector.state["documents"]["evo|" + PDF] = {
        "slug": "evo",
        "name": "Autel EVO",
        "url": PDF,
        "title": "Manual",
        "page_url": ROOT,
    }
    collector.download()
    with (tmp_path / "文档索引.csv").open(encoding="utf-8-sig") as file:
        row = next(csv.DictReader(file))
    assert row["status"] == "failed" and not row["local_path"] and not row["sha256"]
    assert not list(tmp_path.rglob("*.pdf"))
