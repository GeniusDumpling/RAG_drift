"""Serve the G6 build and proxy read-only API calls to the unchanged production app."""

import http.client
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

DIST = Path(os.environ.get("PREVIEW_DIST", "/preview/dist")).resolve()
UPSTREAM = urlsplit(os.environ.get("PREVIEW_UPSTREAM", "http://intelligence-rag-app:8000"))
API_PREFIXES = (
    "/health",
    "/sources",
    "/scheduled-tasks",
    "/jobs",
    "/runs",
    "/contents",
    "/search",
    "/answer",
    "/search-queries",
    "/literature-",
    "/database",
    "/supplier",
    "/drones",
    "/video-graph",
    "/chat",
    "/api/",
)


class Handler(BaseHTTPRequestHandler):
    def error_json(self, status, message):
        body = json.dumps({"detail": message}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def proxy(self):
        connection = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port or 80, timeout=90)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 65536:
                self.error_json(413, "请求内容过大")
                return
            body = self.rfile.read(size) if size else None
            headers = {
                name: self.headers[name]
                for name in ("Accept", "Content-Type")
                if name in self.headers
            }
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (OSError, http.client.HTTPException, ValueError):
            self.error_json(502, "预览无法连接现有后端，请检查正式应用是否运行")
        finally:
            connection.close()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/preview-health":
            self.error_json(200, "ok")
            return
        if path.startswith(API_PREFIXES):
            self.proxy()
            return
        from urllib.parse import unquote

        target = (DIST / unquote(path).lstrip("/")).resolve()
        if not target.is_relative_to(DIST):
            self.error_json(403, "无法访问该路径")
            return
        if not target.is_file():
            if Path(path).suffix:
                self.error_json(404, "文件不存在")
                return
            target = DIST / "index.html"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Cache-Control",
            "no-store" if target.name == "index.html" else "public, max-age=31536000, immutable",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if urlsplit(self.path).path == "/video-graph/retrieve":
            self.proxy()
        else:
            self.error_json(405, "预览仅用于图谱浏览，请到现有版本执行写入操作")

    def do_DELETE(self):
        self.error_json(405, "预览仅用于图谱浏览")

    do_PATCH = do_DELETE
    do_PUT = do_DELETE


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
