import csv
import importlib
from types import SimpleNamespace

import pymupdf
import pytest

from tools.drone_documents.common import digest, read_jsonl, validate_bundle
from tools.drone_documents.extract import selected_text
from tools.parrot_documents.catalog import Catalog
from tools.parrot_documents.cli import enrich_bundle, main
from tools.parrot_documents.prepare import prepare, version_metadata


def write_index(root, name, rows):
    fields = sorted({k for row in rows for k in row})
    with (root / name).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_old_and_new_version_stamps_keep_publication_and_update_separate():
    old = version_metadata("User Guide v6.7.0.1 – published on October 23, 2020\n3")
    assert old["official_version"] == "6.7.0.1"
    assert old["published_at"] == "2020-10-23"
    assert old["updated_at"] is None
    new = version_metadata("Version: 8.5.0.0\nUpdated: October 1st, 2026\n")
    assert new["updated_at"] == "2026-10-01"
    assert new["published_at"] is None


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((40, 40), "ANAFI Ai User Guide")
    page.insert_text((40, 75), "Version: 7.7.1.7\nUpdated: April 02, 2025")
    page = pdf.new_page()
    page.insert_text((40, 70), "Check the battery before flight. Keep the propellers clear.")
    page.draw_rect(pymupdf.Rect(60, 150, 300, 270))
    page.draw_line((75, 180), (260, 180))
    pdf.set_toc([[1, "Getting started", 1], [2, "Preflight checklist", 2]])
    raw = pdf.tobytes()
    pdf.close()
    (root / "manual.pdf").write_bytes(raw)
    common = {
        "url": "https://www.parrot.com/manual.pdf",
        "sha256": digest(raw),
        "local_path": "manual.pdf",
        "status": "existing",
        "bytes": str(len(raw)),
    }
    write_index(
        root,
        "文档索引.csv",
        [
            {**common, "slug": "anafi-ai", "title": "ANAFI Ai User Guide"},
            {**common, "slug": "anafi-usa", "title": "ANAFI USA User Guide"},
            {
                "slug": "bebop-range",
                "title": "Bebop User Guide",
                "status": "failed",
                "url": "https://www.parrot.com/missing.pdf",
                "error": "404",
            },
        ],
    )
    code = 'def fly(drone):\n    if drone.connected:\n        print("<ready>")\n'
    html = (
        '<html><body><p>Source: https://developer.parrot.com/docs/olympe/test.html</p>'
        '<main><section id="flight"><h1>Flight API</h1><h2 id="takeoff">Take off</h2>'
        '<p>Call <code>takeoff()</code> after the preflight checks.</p>'
        f'<pre>{code.replace("<", "&lt;").replace(">", "&gt;")}</pre>'
        '<table><tr><th rowspan="2">Mode</th><th>Limit</th></tr>'
        '<tr><td>10 m</td></tr></table>'
        '<p>[图像: takeoff-diagram]</p><p>&lt;script&gt;alert(1)&lt;/script&gt;</p>'
        '</section></main></body></html>'
    ).encode()
    folder = root / "网页文档" / "sdk-olympe"
    folder.mkdir(parents=True)
    (folder / "page.html").write_bytes(html)
    write_index(
        root,
        "网页采集记录.csv",
        [
            {
                "url": "https://developer.parrot.com/docs/olympe/test.html",
                "title": "Flight API",
                "local_path": "网页文档\\sdk-olympe\\page.html",
                "sha256": digest(html),
                "status": "existing",
            },
            {
                "url": "https://developer.parrot.com/docs/sphinx/missing.html",
                "status": "failed",
                "error": "403",
            },
        ],
    )
    return root, code


def test_parrot_indexes_dedup_and_failures(archive):
    root, _ = archive
    catalog = Catalog(root)
    rows = catalog.select()
    assert len(rows) == 2
    pdf = next(r for r in rows if r["kind"] == "pdf")
    assert pdf["document_type"] == "user_manual"
    assert {r["scope"] for r in pdf["aliases"]} == {"anafi-ai", "anafi-usa"}
    assert len(catalog.failures) == 2
    assert catalog.failures[-1]["scope"] == "sdk-sphinx"
    assert catalog.select(["anafi-ai"], "pdf")[0] == pdf
    with pytest.raises(ValueError, match="未知 scope"):
        catalog.select(["not-a-drone"])


def test_english_pdf_and_html_are_citable_and_code_exact(archive, tmp_path):
    root, code = archive
    output = tmp_path / "bundle"
    prepare(root, output, ocr="off", chunk_max_chars=200)
    manifest = validate_bundle(output)
    assert manifest["config"]["language_policy"] == "preserve-source-languages"
    documents = read_jsonl(output / "documents.jsonl")
    assert len(documents) == 2
    doc = next(d for d in documents if d["metadata_json"]["source_kind"] == "pdf")
    assert doc["official_version"] == "7.7.1.7"
    assert doc["metadata_json"]["updated_at"] == "2025-04-02"
    assert doc["published_at"] is None
    assert doc["model_slugs"] == ["parrot:anafi-ai", "parrot:anafi-usa"]
    pages = read_jsonl(output / "pages.jsonl")
    assert [p["source_page_no"] for p in pages if p["included"]] == [1, 2]
    elements = read_jsonl(output / "elements.jsonl")
    assert any(e["element_type"] == "figure" for e in elements)
    english = next(e for e in elements if "Check the battery" in e["text"])
    assert english["section_path"] == "Getting started / Preflight checklist"
    assert english["regions"][0]["source_page_no"] == 2
    assert next(e for e in elements if e["element_type"] == "code")["text"] == code
    table = next(e for e in elements if e["element_type"] == "table")
    assert table["table_json"]["rows"] == [["Mode", "Limit"], ["Mode", "10 m"]]
    chunks = read_jsonl(output / "chunks.jsonl")
    assert any("Check the battery" in c["display_text"] for c in chunks)
    assert "".join(c["display_text"] for c in chunks if c["chunk_type"] == "code") == code
    assert all("source_start" in c["metadata_json"] for c in chunks)
    by_id = {e["id"]: e for e in elements}
    for chunk in chunks:
        for span in chunk["metadata_json"]["source_spans"]:
            original = by_id[span["element_id"]]["text"]
            assert (
                original[span["element_start"] : span["element_end"]]
                == chunk["display_text"][span["chunk_start"] : span["chunk_end"]]
            )
    assert any(
        c["metadata_json"].get("source_locator", "").endswith("#flight")
        for c in chunks
        if c["chunk_type"] == "code"
    )
    assert all(m["manufacturer"] == "Parrot" for m in read_jsonl(output / "models.jsonl"))
    assert len(read_jsonl(output / "collection_failures.jsonl")) == 2
    assert main(["report", "--bundle", str(output)]) == 0
    preview = (output / "preview.html").read_text()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in preview
    assert "<script>alert(1)</script>" not in preview
    # The original DJI language policy stays unchanged.
    assert selected_text(english["text"], dominant=False, marker=None) == ""


def test_checksum_mismatch_fails_before_output(archive, tmp_path):
    root, _ = archive
    (root / "manual.pdf").write_bytes(b"corrupted")
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="哈希"):
        prepare(root, output)
    assert not output.exists()


def test_path_escape_is_rejected(archive):
    root, _ = archive
    write_index(
        root,
        "文档索引.csv",
        [
            {
                "slug": "anafi-ai",
                "title": "Guide",
                "status": "existing",
                "local_path": "..\\outside.pdf",
                "sha256": "123",
                "url": "https://www.parrot.com/a.pdf",
            }
        ],
    )
    with pytest.raises(ValueError, match="越界"):
        Catalog(root)


def test_long_code_can_be_reassembled_without_losing_indent(archive, tmp_path):
    root, _ = archive
    path = root / "网页文档/sdk-olympe/page.html"
    code = "def mission():\n" + "    drone.takeoff()\n" * 80
    raw = f"<main><h1 id='api'>Mission</h1><pre>{code}</pre></main>".encode()
    path.write_bytes(raw)
    write_index(
        root,
        "网页采集记录.csv",
        [
            {
                "title": "Mission API",
                "status": "existing",
                "sha256": digest(raw),
                "local_path": "网页文档/sdk-olympe/page.html",
                "url": "https://developer.parrot.com/docs/olympe/test.html",
            }
        ],
    )
    output = tmp_path / "code"
    prepare(root, output, kind="html", chunk_max_chars=200)
    chunks = [c for c in read_jsonl(output / "chunks.jsonl") if c["chunk_type"] == "code"]
    assert len(chunks) > 1
    assert "".join(c["display_text"] for c in chunks) == code
    assert all(len(c["display_text"]) <= 200 for c in chunks)


def test_shared_identity_is_stable_between_all_and_subset(archive, tmp_path):
    root, _ = archive
    outputs = [tmp_path / "all", tmp_path / "selected"]
    prepare(root, outputs[0], kind="pdf", ocr="off", figures=False, render_pages=False)
    prepare(
        root,
        outputs[1],
        scopes=["anafi-usa"],
        kind="pdf",
        ocr="off",
        figures=False,
        render_pages=False,
    )
    assert read_jsonl(outputs[0] / "documents.jsonl") == read_jsonl(outputs[1] / "documents.jsonl")
    assert read_jsonl(outputs[0] / "chunks.jsonl") == read_jsonl(outputs[1] / "chunks.jsonl")


def test_interrupted_prepare_resumes_completed_documents(archive, tmp_path, monkeypatch):
    root, _ = archive
    module = importlib.import_module("tools.parrot_documents.prepare")
    original = module.html_elements

    def interrupted(*args):
        raise RuntimeError("interrupted after PDF")

    monkeypatch.setattr(module, "html_elements", interrupted)
    output = tmp_path / "resume"
    kwargs = {"ocr": "off", "figures": False, "render_pages": False}
    with pytest.raises(RuntimeError, match="interrupted"):
        prepare(root, output, **kwargs)
    assert len(list((output / "checkpoints").glob("*.json"))) == 1
    monkeypatch.setattr(module, "html_elements", original)

    def must_not_parse_again(*args):
        pytest.fail("completed PDF was parsed again")

    monkeypatch.setattr(module, "pdf_elements", must_not_parse_again)
    prepare(root, output, resume=True, **kwargs)
    before = validate_bundle(output)
    assert len(read_jsonl(output / "documents.jsonl")) == 2
    assert prepare(root, output, resume=True, **kwargs) == before
    with pytest.raises(ValueError, match="配置或输入索引已变化"):
        prepare(root, output, resume=True, chunk_max_chars=500, **kwargs)


@pytest.mark.asyncio
async def test_no_figure_enrichment_keeps_english_without_model_calls(
    archive, tmp_path, monkeypatch
):
    root, _ = archive
    from tools.drone_documents import enrich

    async def must_not_call_model(*args, **kwargs):
        pytest.fail("text-only bundle requested a vision model")

    monkeypatch.setattr(enrich, "chat_json", must_not_call_model)
    output = tmp_path / "text"
    prepare(root, output, kind="html", figures=False)
    before = read_jsonl(output / "chunks.jsonl")
    result = await enrich_bundle(output, 1)
    assert not result["enrichment_failures"]
    assert validate_bundle(output)["stage"] == "enriched"
    assert read_jsonl(output / "chunks.jsonl") == before


@pytest.mark.asyncio
async def test_query_does_not_filter_out_english_or_apply_dji_software_rules(
    archive, tmp_path, monkeypatch
):
    from tools.drone_documents import storage

    root, _ = archive
    output = tmp_path / "query"
    manifest = prepare(root, output, scopes=["sdk-olympe"], kind="html", figures=False)
    calls = {}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            calls["scope_params"] = params
            return [
                {"active_processing_id": read_jsonl(output / "documents.jsonl")[0]["processing_id"]}
            ]

    class Client:
        def __init__(self, **kwargs):
            pass

        def search(self, *args, **kwargs):
            calls["filter"] = kwargs["query_filter"]
            return []

        def close(self):
            pass

    async def embed(*args):
        return [[0.0] * 1024]

    monkeypatch.setattr(storage, "configuration", lambda *a: ("test", "test", "test"))
    monkeypatch.setattr(storage.psycopg, "connect", lambda *a, **kw: Connection())
    monkeypatch.setattr(storage, "QdrantClient", Client)
    monkeypatch.setattr(storage, "_embed_texts", embed)
    monkeypatch.setattr(storage, "get_settings", lambda: SimpleNamespace())
    await storage.query(output, "How does this API resolve the drone connection?")
    assert calls["scope_params"] == (manifest["scope_slugs"],)
    assert [condition.key for condition in calls["filter"].must] == ["processing_id"]
