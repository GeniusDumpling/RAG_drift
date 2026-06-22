#!/usr/bin/env python3
"""Serve built frontend assets and proxy API paths to a local FastAPI server."""

from __future__ import annotations

import argparse
import http.client
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

API_PREFIXES = (
    "/health",
    "/sources",
    "/jobs",
    "/runs",
    "/contents",
    "/search",
    "/answer",
    "/search-queries",
    "/database",
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def is_api_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in API_PREFIXES)


class DemoProxyHandler(SimpleHTTPRequestHandler):
    api_base = "http://127.0.0.1:18000"
    dist_dir = "/root/intelligence-rag/frontend/dist"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=self.dist_dir, **kwargs)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def do_OPTIONS(self):
        self._handle()

    def _handle(self):
        parsed = urlsplit(self.path)
        if is_api_path(parsed.path):
            self._proxy_to_api()
        else:
            self._serve_static_or_spa()

    def _proxy_to_api(self):
        target = urlsplit(self.api_base)
        body_len = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(body_len) if body_len else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        headers["Host"] = target.netloc
        connection_class = (
            http.client.HTTPSConnection
            if target.scheme == "https"
            else http.client.HTTPConnection
        )
        port = target.port or (443 if target.scheme == "https" else 80)
        connection = None
        try:
            connection = connection_class(target.hostname, port, timeout=20)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("X-Intel-RAG-Proxy", "demo-static-proxy")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001 - surface proxy failures to browser.
            self.send_response(502, "Bad Gateway")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"API proxy failed: {exc}\n".encode())
        finally:
            if connection is not None:
                connection.close()

    def _serve_static_or_spa(self):
        parsed = urlsplit(self.path)
        requested_path = parsed.path.lstrip("/")
        filesystem_path = os.path.join(self.dist_dir, requested_path)
        if parsed.path == "/" or os.path.isfile(filesystem_path):
            return super().do_GET()
        self.path = "/index.html"
        return super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--dist", default="/root/intelligence-rag/frontend/dist")
    parser.add_argument("--api", default="http://127.0.0.1:18000")
    args = parser.parse_args()

    DemoProxyHandler.dist_dir = args.dist
    DemoProxyHandler.api_base = args.api.rstrip("/")
    server = ThreadingHTTPServer((args.host, args.port), DemoProxyHandler)
    print(
        f"Serving {args.dist} on http://{args.host}:{args.port}, "
        f"proxying API to {DemoProxyHandler.api_base}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
