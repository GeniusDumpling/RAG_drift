import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools/supplier-information/baidu_search.py"
SPEC = importlib.util.spec_from_file_location("supplier_baidu_search", MODULE_PATH)
assert SPEC and SPEC.loader
baidu_search = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baidu_search)


def _baidu_landing_page(content_data: dict) -> str:
    payload = {
        "errno": 0,
        "data": {
            "pageInfo": {
                "common": {"title": "大疆供应商关系报道"},
                "content": {"data": content_data},
            }
        },
    }
    return f"<script>window.jsonData = {json.dumps(payload, ensure_ascii=False)};</script>"


def test_extracts_text_from_baidu_landing_page_json() -> None:
    page = _baidu_landing_page(
        {
            "title": [{"type": "text", "content": "大疆供应商关系报道"}],
            "content": [
                {
                    "type": "text",
                    "content": "甲公司向大疆供应相机模组。该合作已由双方公开披露。",
                }
            ],
        }
    )

    assert baidu_search.extract_baidu_landing_text(page) == (
        "大疆供应商关系报道\n\n甲公司向大疆供应相机模组。该合作已由双方公开披露。"
    )


def test_rejects_image_only_baidu_landing_page() -> None:
    page = _baidu_landing_page(
        {
            "title": [{"type": "text", "content": "大疆核心供应商全解析"}],
            "image_items": [{"image_url": "https://pics.example/image.jpg"}],
        }
    )

    assert baidu_search.extract_baidu_landing_text(page) == ""


def test_lists_high_resolution_images_from_baidu_landing_page() -> None:
    page = _baidu_landing_page(
        {
            "image_items": [
                {
                    "image_url": "https://pics.example/preview.jpg",
                    "hd_image_url": "https://pics.example/original.jpg",
                }
            ]
        }
    )

    assert baidu_search.extract_baidu_landing_image_urls(page) == [
        "https://pics.example/original.jpg"
    ]


def test_rejects_common_error_page_text_as_fulltext() -> None:
    assert not baidu_search.is_usable_fulltext("网络不给力，请稍后重试\n返回首页\n问题反馈")
