from __future__ import annotations

import asyncio
import io
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.core.config import Settings

ProgressCallback = Callable[[str, str, dict[str, Any]], Awaitable[None]]

IEEE_HOME = "https://ieeexplore.ieee.org/Xplore/home.jsp"
IEEE_SEARCH = "https://ieeexplore.ieee.org/search/searchresult.jsp"
IEEE_INST_SIGNIN = "https://ieeexplore.ieee.org/servlet/Login?logout=/Xplore/home.jsp"
LOGIN_HINTS = (
    "Sign Out",
    "MyProjects",
    "Personal Account",
    "Beijing University of Posts",
)
BODY_SELECTORS = (
    "section#full-text-section",
    "div.document-main",
    "main",
    "article",
    "#article",
)


class IEEEProvider:
    def __init__(self, settings: Settings, progress: ProgressCallback) -> None:
        self.settings = settings
        self.progress = progress
        self._playwright: Any = None
        self.context: Any = None

    async def __aenter__(self) -> IEEEProvider:
        try:
            from playwright.async_api import async_playwright  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                '缺少 Playwright。请执行 pip install -e ".[literature]"，然后执行 '
                "playwright install chromium。"
            ) from exc
        profile = Path(self.settings.literature_browser_profile_dir).expanduser().resolve()
        profile.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self.context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=self.settings.literature_headless,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled"],
            )
            await self.ensure_logged_in()
        except Exception:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_args: Any) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def ensure_logged_in(self) -> None:
        page = await self.context.new_page()
        try:
            await page.goto(IEEE_HOME, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(1500)
            if await _looks_logged_in(page):
                await self.progress("searching", "已复用 IEEE 机构登录态。", {"logged_in": True})
                return
            if self.settings.literature_headless:
                raise RuntimeError(
                    "当前 IEEE 浏览器配置尚未登录，LITERATURE_HEADLESS=true 无法完成首次登录。"
                )
            await self.progress(
                "waiting_for_login",
                "请在弹出的 IEEE 浏览器窗口完成机构登录；检测到登录后将自动继续。",
                {"logged_in": False},
            )
            await page.goto(IEEE_INST_SIGNIN, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + self.settings.literature_login_timeout_seconds
            while time.monotonic() < deadline:
                if await _looks_logged_in(page):
                    await self.progress(
                        "searching", "IEEE 机构登录成功，继续检索。", {"logged_in": True}
                    )
                    return
                await asyncio.sleep(2)
            raise RuntimeError("等待 IEEE 机构登录超时，请重新运行任务并在浏览器中完成登录。")
        finally:
            await page.close()

    async def search(self, query: str, page_size: int) -> list[dict[str, Any]]:
        page = await self.context.new_page()
        try:
            url = f"{IEEE_SEARCH}?newsearch=true&queryText={quote(query)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(1500)
            body = {
                "queryText": query,
                "highlight": True,
                "returnFacets": ["ALL"],
                "returnType": "SEARCH",
                "pageNumber": 1,
                "rowsPerPage": page_size,
                "sortField": "Relevance",
                "sortType": "desc",
            }
            script = """
            async ({body}) => {
                const response = await fetch('/rest/search', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify(body),
                });
                const text = await response.text();
                let data = null;
                try { data = JSON.parse(text); } catch (_) {}
                return {
                    ok: response.ok,
                    status: response.status,
                    data,
                    preview: text.slice(0, 300),
                };
            }
            """
            result = await page.evaluate(script, {"body": body})
            if not result.get("ok"):
                raise RuntimeError(f"IEEE /rest/search 返回 HTTP {result.get('status')}")
            data = result.get("data") or {}
            records = data.get("records") if isinstance(data, dict) else None
            return (
                [row for row in records if isinstance(row, dict)]
                if isinstance(records, list)
                else []
            )
        finally:
            await page.close()

    async def fetch_document(self, record: dict[str, Any]) -> dict[str, Any]:
        article_number = _article_number(record)
        if not article_number:
            return {"pdf": None, "pages": [], "text": "", "source_url": None}
        document_url = f"https://ieeexplore.ieee.org/document/{article_number}"
        pdf_url = (
            "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=" f"{article_number}"
        )
        html_text = await self._scrape_html(document_url)
        pdf_bytes = await self._download_pdf(pdf_url)
        pages: list[dict[str, Any]] = []
        if pdf_bytes:
            try:
                pages = await asyncio.to_thread(_extract_pdf_pages, pdf_bytes)
            except Exception as exc:
                await self.progress(
                    "extracting",
                    f"PDF 正文提取失败，将使用 HTML 正文：{type(exc).__name__}",
                    {"article_number": article_number},
                )
        if not pages and html_text:
            pages = [{"page": None, "text": html_text}]
        fulltext = "\n\n".join(str(page.get("text") or "") for page in pages).strip()
        return {
            "pdf": pdf_bytes,
            "pages": pages,
            "text": fulltext,
            "source_url": document_url,
            "pdf_url": pdf_url,
        }

    async def _scrape_html(self, url: str) -> str:
        page = await self.context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(1200)
            best = ""
            for selector in BODY_SELECTORS:
                try:
                    element = await page.query_selector(selector)
                    if element is None:
                        continue
                    text = await element.inner_text(timeout=5000)
                    if len(text) > len(best):
                        best = text
                except Exception:
                    continue
            return _clean_text(best)
        except Exception:
            return ""
        finally:
            await page.close()

    async def _download_pdf(self, url: str) -> bytes | None:
        try:
            response = await self.context.request.get(url, timeout=60_000)
        except Exception:
            return None
        if not response.ok:
            return None
        body = bytes(await response.body())
        if not body.startswith(b"%PDF-"):
            return None
        if len(body) > self.settings.literature_pdf_max_bytes:
            raise RuntimeError(f"PDF 大小 {len(body)} 字节，超过 LITERATURE_PDF_MAX_BYTES 限制。")
        return body


async def _looks_logged_in(page: Any) -> bool:
    try:
        text = await page.inner_text("body", timeout=5000)
    except Exception:
        return False
    return any(hint.casefold() in text.casefold() for hint in LOGIN_HINTS)


def _article_number(record: dict[str, Any]) -> str:
    for key in ("articleNumber", "arnumber", "documentId", "documentNumber"):
        value = record.get(key)
        if value:
            return str(value)
    return ""


def _clean_text(value: str) -> str:
    value = re.sub(r"[ \t]+", " ", value or "")
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _extract_pdf_pages(data: bytes) -> list[dict[str, Any]]:
    try:
        from pdfminer.high_level import extract_pages  # type: ignore[import-not-found]
        from pdfminer.layout import LTTextContainer  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError('缺少 pdfminer.six，请安装项目的 "literature" 可选依赖。') from exc
    pages = []
    for page_number, layout in enumerate(extract_pages(io.BytesIO(data)), 1):
        chunks = []
        for element in layout:
            if isinstance(element, LTTextContainer):
                chunks.append(element.get_text())
        text = _clean_text("\n".join(chunks))
        if text:
            pages.append({"page": page_number, "text": text})
    return pages
