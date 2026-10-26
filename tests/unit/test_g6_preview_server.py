"""The preview serves static assets and never forwards mutating business requests."""

import importlib.util
import json
import threading
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest


@pytest.fixture
def preview(tmp_path, monkeypatch):
    calls = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path))
            data = b'{"nodes":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_POST = do_GET

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    module_path = Path(__file__).resolve().parents[2] / "docker/g6-preview-server.py"
    spec = importlib.util.spec_from_file_location("g6_preview_server", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "index.html").write_text("<main>preview</main>")
    (tmp_path / "app.js").write_text("export const preview=true;")
    monkeypatch.setattr(module, "DIST", tmp_path)
    monkeypatch.setattr(module, "UPSTREAM", urlsplit(f"http://127.0.0.1:{upstream.server_port}"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    for item in (upstream, server):
        threading.Thread(target=item.serve_forever, daemon=True).start()

    def request(method, path, body=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(method, path, body=body)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        return status, data

    yield request, calls
    for item in (server, upstream):
        item.shutdown()
        item.server_close()


def test_preview_proxy_and_static_routes(preview):
    request, calls = preview
    assert request("GET", "/?view=VideoGraph") == (200, b"<main>preview</main>")
    assert request("GET", "/video-graph/graph?q=GPS")[0] == 200
    assert calls == [("GET", "/video-graph/graph?q=GPS")]
    assert request("POST", "/video-graph/retrieve", "{}")[0] == 200
    assert calls[-1] == ("POST", "/video-graph/retrieve")
    assert request("HEAD", "/app.js") == (200, b"")
    assert request("GET", "/missing.js")[0] == 404
    assert request("GET", "/%2e%2e/secret.txt")[0] == 403


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/scheduled-tasks"),
        ("POST", "/answer"),
        ("DELETE", "/contents/test"),
        ("PATCH", "/sources/test"),
    ],
)
def test_preview_rejects_writes_before_they_reach_production(preview, method, path):
    request, calls = preview
    status, body = request(method, path, "{}")
    assert status == 405
    assert "预览" in json.loads(body)["detail"]
    assert not calls
