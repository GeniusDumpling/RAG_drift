"""Browser acceptance for source-bound Mavic 3 Pro chip evidence and saved chat images."""

import argparse
import hashlib
import json
import re
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from .bundle import load_bundle
from .common import ROOT


def verify_sources(base_url: str, result: dict) -> list[dict]:
    bundle = load_bundle(ROOT / "data/processed/ifixit/20261021-production-bge-m3")
    board_steps = {
        step["step_occurrence_id"]
        for step in bundle["steps"]
        if step["document_id"] == "ifixit:161708:en"
        and step["title"] == "Drone Main Board Side A"
    }
    assert board_steps, "missing main-board ground truth"
    allowed = {
        ref["source_guid"]: ref
        for ref in bundle["media_refs"]
        if ref["document_id"] == "ifixit:161708:en"
        and (
            ref.get("step_occurrence_id") in board_steps
            # Visually checked annotated main-board cover, not another board
            # from this guide (which also includes camera/controller photos).
            or ref["role"] == "cover" and ref["source_guid"] == "HB5apup35uvAbdWi"
        )
    }
    checked = {}
    with httpx.Client(base_url=base_url, timeout=30) as client:
        response = client.get(f"/chat-sessions/{result['session_id']}")
        response.raise_for_status()
        turn = response.json()["turns"][-1]
        assert turn["answer"] == result["answer"]
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", turn["answer"])}
        for index, evidence in enumerate(turn["supporting_evidence"], 1):
            if index not in cited:
                continue
            for asset in evidence.get("images", []):
                ref = allowed.get(asset["source_guid"])
                assert ref and asset["source_url"] == ref["source_url"], asset
                assert (
                    asset["guide_url"]
                    == bundle["guides_by_id"]["ifixit:161708:en"]["source_url"]
                )
                response = client.get(asset["url"])
                response.raise_for_status()
                assert hashlib.sha256(response.content).hexdigest() == asset["asset_id"]
                checked[asset["url"]] = {
                    "source_guid": asset["source_guid"],
                    "sha256": asset["asset_id"],
                    "citation": index,
                }
    assert checked and {image["src"] for image in result["answer_images"]} == set(checked)
    return list(checked.values())


def verify(base_url: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("button", name="AI 问答", exact=True).click()
        page.get_by_role("textbox", name="输入问题").fill("DJI Mavic 3 pro的主板芯片是什么型号")
        with page.expect_response(
            lambda r: "/answer/stream" in r.url and r.request.method == "POST", timeout=180000
        ) as response:
            page.get_by_role("button", name="发送", exact=True).click()
        page.get_by_role("button", name="发送", exact=True).wait_for(
            state="visible", timeout=180000
        )
        message = page.locator(".chat-message-assistant").last
        message.scroll_into_view_if_needed()
        image_selector = ".chat-message-body > .evidence-images img"
        images = message.locator(image_selector)
        info = images.evaluate_all(
            "nodes => nodes.map(n => ({alt:n.alt,src:n.getAttribute('src'),"
            "complete:n.complete,width:n.naturalWidth}))"
        )
        result = {
            "answer": message.locator(".chat-message-text").inner_text(),
            "answer_images": info,
            "page_errors": errors,
            "stream_status": response.value.status,
            "answer_label": message.locator(".chat-role").inner_text(),
        }
        page.screenshot(path=str(output / "chat.png"), full_page=True)
        (output / "browser-report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        assert images.count() > 0, "no inline cited images"
        assert all(
            "Mavic 3 Pro" in image["alt"]
            and "Mini 3" not in image["alt"]
            and image["complete"]
            and image["width"] > 300
            for image in info
        ), info
        assert "H6MN" in result["answer"] and (
            "可能" in result["answer"] or "Likely" in result["answer"]
        )
        assert result["answer_label"] == "基于证据的回答" and not errors
        # A stored conversation must preserve the same image URLs and source captions.
        page.reload(wait_until="networkidle")
        page.get_by_role("button", name="AI 问答", exact=True).click()
        restored = page.locator(".chat-message-assistant").last
        restored.locator(".chat-message-body > .evidence-images img").first.wait_for(timeout=30000)
        restored_urls = restored.locator(image_selector).evaluate_all(
            "nodes => nodes.map(n => n.getAttribute('src'))"
        )
        assert restored_urls == [image["src"] for image in info]
        result["restored_images_verified"] = True
        result["session_id"] = page.evaluate("localStorage.getItem('rag-chat-session')")
        result["source_assets_verified"] = verify_sources(base_url, result)
        (output / "browser-report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        browser.close()
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.base_url, args.output), ensure_ascii=False))
