import io
import json
import zipfile

import fitz
import pytest
from bs4 import BeautifulSoup

from tools.autel_documents.collector import FetchError
from tools.drone_collection.archive import Archive, policy
from tools.drone_collection.pdf_specs import extract_pdf_specs
from tools.hubsan_documents.collector import build as build_hubsan
from tools.hubsan_documents.collector import document_language as hubsan_language
from tools.hubsan_documents.collector import extract_product_specs, model_slug, product_identity
from tools.skydio_documents.collector import document_language as skydio_language
from tools.skydio_documents.collector import extract_tables, scope


def pdf_bytes():
    with fitz.open() as pdf:
        pdf.new_page().insert_text((20, 30), "Public operator manual fixture")
        return pdf.tobytes()


def test_brand_host_policies_do_not_trust_unrelated_redirects():
    hubsan = policy(["hubsan.com"])
    skydio = policy(["skydio.com"], ["cdn.sanity.io"])
    assert hubsan("https://chn-filemanger.hubsan.com/index/index/getSupport.html")
    assert skydio("https://support.skydio.com/hc/article_attachments/123", document=True)
    assert skydio("https://cdn.sanity.io/files/official.pdf", document=True)
    assert not skydio("https://cdn.sanity.io/not-a-document")
    assert not hubsan("https://hubsan.com.evil.test/manual.pdf", document=True)
    assert not skydio("http://127.0.0.1/private.pdf", document=True)


def test_shared_pdf_is_fetched_once_and_corrupt_cache_is_repaired(tmp_path):
    archive = Archive(tmp_path, "Hubsan", ["hubsan.com"])
    url = "https://www.hubsan.com/manual.pdf"
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return pdf_bytes(), url, {}

    archive.client.fetch = fetch
    for slug in ["model-a", "model-b"]:
        archive.add_document(url, slug, slug, "Manual", "https://www.hubsan.com/support/")
    archive.download()
    assert calls == [url]
    assert archive.export()["unique_pdfs"] == 1
    assert archive.verify()["ok"]
    row = next(iter(archive.state["documents"].values()))
    (archive.root / row["local_path"]).write_bytes(b"corrupt")
    assert not archive.verify()["ok"]
    archive.download()
    assert calls == [url, url] and archive.verify()["ok"]


def test_zip_manuals_are_flattened_and_only_pdf_members_are_published(tmp_path):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as z:
        z.writestr("../../outside.pdf", pdf_bytes())
        z.writestr("firmware.bin", b"not a document")
    archive = Archive(tmp_path, "Hubsan", ["hubsan.com"])
    url = "https://www.hubsan.com/manual.zip"
    archive.client.fetch = lambda *args, **kwargs: (content.getvalue(), url, {})
    archive.add_document(url, "model", "Model", "User Manual", url, kind="zip")
    archive.download()
    members = [r for r in archive.state["documents"].values() if r.get("archive_member")]
    assert len(members) == 1 and members[0]["archive_member"] == "../../outside.pdf"
    assert (tmp_path / members[0]["local_path"]).is_relative_to(tmp_path)
    assert not (tmp_path.parent / "outside.pdf").exists()
    assert not list(tmp_path.rglob("*.bin"))
    assert archive.verify()["ok"]


def test_json_failure_is_explicit_and_not_cached(tmp_path):
    archive = Archive(tmp_path, "Skydio", ["skydio.com"])
    archive.client.fetch = lambda *a, **kw: (_ for _ in ()).throw(FetchError("HTTP 503", 503))
    row = archive.fetch("https://support.skydio.com/api/categories.json", kind="json")
    assert row["status"] == "failed" and row["error"] == "HTTP 503"
    assert not row["local_path"] and archive.export()["failed_pages"] == 1


def test_hubsan_aliases_preserve_actual_variants_and_specs_do_not_include_cart_weights():
    assert model_slug("黑鹰2号内置版") == model_slug("BLACKHAWK 2(4G)")
    assert model_slug("HUBSAN ACE PRO 精英版") != model_slug("HUBSAN ACE PRO")
    assert model_slug("Zino with an extra Battery") == model_slug("ZINO")
    groups = extract_product_specs("""<html><div>Shopping weight: 0g</div>
    <regsub><ul><li>Max Flight Time: 20 minutes (windless)</li>
    <li>Flying Weight- 450g</li></ul></regsub>
    <div class="part_box"><ul><li>Max Flight Time: 20 minutes (windless)</li></ul></div></html>""")
    values = groups[0]["parameters"]
    assert len(values) == 2
    assert values[0]["value"] == "20 minutes (windless)"
    assert values[1]["value"] == "450g"


def test_skydio_tables_preserve_configuration_and_outside_table_conditions():
    source = """<article class="basic-tables"><header>Skydio X10</header>
    <table class="basic-tables__table"><thead><tr><th>Aircraft</th></tr></thead><tbody>
    <tr><td>Weight</td><td>Connect SL: 2.11 kg<br>Connect SL + 5G: 2.16 kg</td></tr>
    <tr><td>Max flight time*</td><td>40 minutes</td></tr></tbody></table>
    <p>*Measured without wind in the specified configuration.</p></article>"""
    groups = extract_tables(source)
    assert groups[0]["parameters"][0]["value"] == "Connect SL: 2.11 kg\nConnect SL + 5G: 2.16 kg"
    assert "without wind" in groups[1]["parameters"][0]["value"]
    assert scope("Skydio R10 Guide") == "r10"
    assert scope("Skydio R1 flight tips") == "r1"
    assert scope("Skydio X10D Operator Manual") == "x10d"


def test_shared_pdf_separates_skydio_2_and_2_plus_specification_pages(tmp_path):
    path = tmp_path / "manual.pdf"
    with fitz.open() as pdf:
        for model, value in [("Skydio 2", "23 minutes"), ("Skydio 2+", "27 minutes")]:
            page = pdf.new_page()
            page.insert_text((40, 50), "General")
            page.draw_rect(fitz.Rect(40, 60, 400, 140))
            page.draw_line((200, 60), (200, 140))
            page.draw_line((40, 100), (400, 100))
            page.insert_text((45, 82), "FLIGHT TIME")
            page.insert_text((205, 82), value)
            page.insert_text((45, 122), "WEIGHT")
            page.insert_text((205, 122), "775 g")
            page.insert_text((40, 700), model + " Specifications")
        pdf.save(path)
    a = extract_pdf_specs(path, r"Skydio 2 Specifications")
    b = extract_pdf_specs(path, r"Skydio 2\+ Specifications")
    assert a[0]["parameters"][0]["value"] == "23 minutes"
    assert b[0]["parameters"][0]["value"] == "27 minutes"
    assert a[0]["source_page"] == 1 and b[0]["source_page"] == 2


def test_document_language_follows_explicit_markers_not_menu_region():
    assert hubsan_language("https://chn.hubsan.com/files/BLACKHAWK_cn_V26.pdf") == "zh-CN"
    assert hubsan_language("https://www.hubsan.com/files/ZINO_2026.pdf") == "und"
    soup = BeautifulSoup(
        "<p>Japanese</p><ul><li><a>Skydio X10 Operator Manual</a></li></ul>", "html.parser"
    )
    assert skydio_language(soup.a) == "ja"
    soup.a.string = "Skydio X2D Operator Manual - Ukrainian"
    assert skydio_language(soup.a) == "uk"


def test_product_model_matching_does_not_merge_ace_variants_or_mini_generations():
    models = {
        slug: {"aliases": [name]}
        for slug, name in [
            ("ace", "HUBSAN ACE"),
            ("ace-se", "ACE SE"),
            ("ace-pro", "ACE PRO"),
            ("mini", "HUBSAN MINI"),
            ("ace-2-plus", "HUBSAN ACE 2+"),
        ]
    }
    assert product_identity("HUBSAN ACE SE 标准版", models)[0] == "ace-se"
    assert product_identity("HUBSAN ACE PRO 便携版 双电", models)[0] == "ace-pro"
    assert product_identity("ACE2+ 1套餐", models)[0] == "ace-2-plus"
    assert product_identity("HUBSAN MINI 7", models) == (None, "HUBSAN MINI 7")
    assert product_identity("HUBSAN ACE 3 1套餐", models) == (None, "HUBSAN ACE 3")
    assert model_slug("H109S X4 Pro Advanced") == "h109s-advanced"


def test_image_parameters_require_reviewed_archived_bytes(tmp_path):
    archive = Archive(tmp_path, "Hubsan", ["hubsan.com"])
    archive.state["models"]["blackhawk-4"] = {
        "slug": "blackhawk-4",
        "name": "黑鹰4号",
        "product_urls": [],
    }
    images = Archive(tmp_path / "参数图片", "Hubsan", ["hubsan.com"])
    url = "https://chn.hubsan.com/spec.jpg"
    row = images.store(
        url,
        b"original image fixture",
        kind="image",
        title="Specifications",
        slug="blackhawk-4",
        page_url="https://chn.hubsan.com/product",
    )
    images.state["pages"][url] = row
    images.checkpoint()
    reviewed = [
        {
            "slug": "blackhawk-4",
            "reviewed": True,
            "source_sha256": row["sha256"],
            "groups": [{"name": "飞行器", "parameters": [{"name": "重量", "value": "712.7克"}]}],
        }
    ]
    assert build_hubsan(archive, reviewed_records=reviewed)["model_count"] == 1
    model = json.loads((tmp_path / "机型参数目录/blackhawk-4/技术参数.json").read_text())
    assert model["groups"][0]["parameters"][0]["value"] == "712.7克"
    assert model["source_sha256"] == row["sha256"] and model["status"] == "partial"
    assert archive.verify()["ok"]
    model_path = tmp_path / "机型参数目录/blackhawk-4/技术参数.json"
    original = model_path.read_bytes()
    model["groups"][0]["parameters"][0]["value"] = "incorrect transcription"
    model_path.write_text(json.dumps(model))
    assert not archive.verify()["ok"]
    model_path.write_bytes(original)
    (images.root / row["local_path"]).write_bytes(b"changed source")
    with pytest.raises(ValueError, match="hash mismatch"):
        build_hubsan(archive, reviewed_records=reviewed)
