#!/usr/bin/env python3
"""Simple local HTTP file server to serve downloaded video files for VLM access."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local file server for VLM video access")
    parser.add_argument(
        "--dir",
        default=None,
        help="Directory to serve (default: skills/video-crawler/downloads)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=18999, help="Port (default: 18999)")
    parser.add_argument(
        "--cors",
        default="*",
        help="Access-Control-Allow-Origin (default: *)",
    )
    args = parser.parse_args()

    # Default: script dir ../../downloads
    serve_dir = Path(args.dir) if args.dir else (
        Path(__file__).resolve().parent.parent / "downloads"
    )
    serve_dir.mkdir(parents=True, exist_ok=True)

    file_list = list(serve_dir.iterdir())
    if file_list:
        logger.info("现有文件 (%d):", len(file_list))
        for f in file_list:
            logger.info("  %s  (%d MB)", f.name, f.stat().st_size // (1024 * 1024))
    else:
        logger.info("目录为空: %s", serve_dir)

    import http.server
    import socketserver

    class CORSHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(serve_dir), **kwargs)

        def end_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", args.cors)
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            super().end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            logger.info("%s - %s", self.client_address[0], format % args)

    handler = CORSHandler
    # 禁用 IPV6 避免双栈问题
    httpd = socketserver.TCPServer((args.host, args.port), handler)

    print(f"文件服务器运行在 http://{args.host}:{args.port}")
    print(f"服务目录: {serve_dir.resolve()}")
    print(f"查看文件列表: http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{args.port}/")
    print("按 Ctrl+C 停止")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止服务器")
        httpd.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
