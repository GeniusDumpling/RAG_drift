from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DiscoveredPage:
    requested_url: str
    context_json: dict[str, Any]


@dataclass(frozen=True)
class FetchedPage:
    requested_url: str
    final_url: str
    http_status: int
    content_type: str
    response_headers_json: dict[str, Any]
    raw_html: str
    raw_markdown: str | None
    raw_json: dict[str, Any] | None


class CrawlAdapter(Protocol):
    def discover(
        self, seed_config_json: dict[str, Any], seed_url: str | None, max_pages: int
    ) -> list[DiscoveredPage]: ...

    def fetch(self, page: DiscoveredPage) -> FetchedPage: ...

    def prepare_extraction_context(
        self, page: DiscoveredPage, fetched: FetchedPage
    ) -> dict[str, Any]: ...


class DeterministicAdapter:
    adapter_name = "deterministic"
    page_kind = "page"

    def discover(
        self, seed_config_json: dict[str, Any], seed_url: str | None, max_pages: int
    ) -> list[DiscoveredPage]:
        if max_pages < 1:
            return []

        urls: list[str] = []
        if seed_url is not None and seed_url.strip():
            urls.append(seed_url.strip())

        configured_urls = seed_config_json.get("urls", [])
        if isinstance(configured_urls, list):
            for configured_url in configured_urls:
                if isinstance(configured_url, str) and configured_url.strip():
                    stripped_url = configured_url.strip()
                    if stripped_url not in urls:
                        urls.append(stripped_url)

        return [
            DiscoveredPage(
                requested_url=url,
                context_json={
                    "adapter": self.adapter_name,
                    "page_kind": self.page_kind,
                    "discovery_index": index,
                },
            )
            for index, url in enumerate(urls[:max_pages])
        ]

    def prepare_extraction_context(
        self, page: DiscoveredPage, fetched: FetchedPage
    ) -> dict[str, Any]:
        return {
            **page.context_json,
            "requested_url": page.requested_url,
            "final_url": fetched.final_url,
            "http_status": fetched.http_status,
            "content_type": fetched.content_type,
        }


class OfficialSiteAdapter(DeterministicAdapter):
    adapter_name = "official_site"
    page_kind = "official_page"

    def fetch(self, page: DiscoveredPage) -> FetchedPage:
        raw_html = (
            "<html><head><title>Deterministic official page</title></head>"
            f"<body><main><h1>Official page for {page.requested_url}</h1>"
            f"<p>Deterministic official-site content fetched from {page.requested_url}.</p>"
            "</main></body></html>"
        )
        raw_markdown = (
            f"# Official page for {page.requested_url}\n\n"
            f"Deterministic official-site content fetched from {page.requested_url}."
        )
        return FetchedPage(
            requested_url=page.requested_url,
            final_url=page.requested_url,
            http_status=200,
            content_type="text/html; charset=utf-8",
            response_headers_json={"x-deterministic-adapter": self.adapter_name},
            raw_html=raw_html,
            raw_markdown=raw_markdown,
            raw_json=None,
        )


class ForumThreadAdapter(DeterministicAdapter):
    adapter_name = "forum_thread"
    page_kind = "forum_thread"

    def fetch(self, page: DiscoveredPage) -> FetchedPage:
        raw_html = (
            "<html><head><title>Deterministic forum thread</title></head>"
            f"<body><article data-thread-url=\"{page.requested_url}\">"
            f"<h1>Forum thread for {page.requested_url}</h1>"
            f"<p>Root post for deterministic thread {page.requested_url}.</p>"
            f"<p>First deterministic comment on {page.requested_url}.</p>"
            "</article></body></html>"
        )
        raw_markdown = (
            f"# Forum thread for {page.requested_url}\n\n"
            f"Root post for deterministic thread {page.requested_url}.\n\n"
            f"First deterministic comment on {page.requested_url}."
        )
        return FetchedPage(
            requested_url=page.requested_url,
            final_url=page.requested_url,
            http_status=200,
            content_type="text/html; charset=utf-8",
            response_headers_json={"x-deterministic-adapter": self.adapter_name},
            raw_html=raw_html,
            raw_markdown=raw_markdown,
            raw_json=None,
        )


_ADAPTERS: dict[str, CrawlAdapter] = {
    "official_site": OfficialSiteAdapter(),
    "forum_thread": ForumThreadAdapter(),
}


def get_adapter(parser_profile: str) -> CrawlAdapter:
    try:
        return _ADAPTERS[parser_profile]
    except KeyError as exc:
        raise ValueError(f"Unsupported parser_profile: {parser_profile}") from exc
