#!/usr/bin/env python3
"""Produce a local native-source review page; no generated captions or model calls."""

import argparse
import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
from app.services.document_graph.source import load_bundle  # noqa: E402


def write_preview(root, output):
    bundle = load_bundle(root)
    sections = []
    for manual in bundle["manuals"]:
        sections.append(
            f'<h1>{html.escape(manual["title"])}</h1><p>SHA-256 {manual["sha256"]}; '
            f'手册版本 {manual["manual_version"]}</p>'
        )
        for number, page in manual["pages"].items():
            sections.append(f"<h2>PDF物理页 {number}</h2>")
            for label, asset_id in [("原始整页", page["asset_id"])] + [
                (f'{r["id"]} bbox={r["bbox"]}', r["asset_id"]) for r in page["regions"]
            ]:
                relative = os.path.relpath(
                    Path(root) / bundle["assets"][asset_id]["storage_key"], Path(output).parent
                )
                sections.append(
                    f"<figure><figcaption>{html.escape(label)}</figcaption>"
                    f'<img src="{html.escape(relative)}" width="650"></figure>'
                )
            sections.append(
                "<table>"
                + "".join(
                    f'<tr><td>{r["id"]}</td><td>{html.escape(r["text"])}</td>'
                    f'<td>{r["bbox"]}</td></tr>'
                    for r in page["lines"]
                )
                + "</table>"
            )
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(
        '<!doctype html><meta charset="utf-8"><title>试点手册原文原图</title>'
        "<style>body{font:16px sans-serif}td{border:1px solid #ccc;padding:5px}"
        "img{max-width:100%}</style>"
        "<p>仅展示PDF原生文本及原始渲染，不含模型描述。页码从1开始。</p>" + "".join(sections),
        encoding="utf-8",
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-bundle", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    write_preview(args.source_bundle, args.output)
