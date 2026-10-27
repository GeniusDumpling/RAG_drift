"""Recover LED-state tables from native PDF geometry, with a source legend.

This deliberately supports circular on/off symbols and radial flashing marks.
Unknown shapes/legends fail closed instead of inferring states from battery ranges.
"""

import re
from collections import defaultdict

import pymupdf


def _box(box):
    return [round(float(x), 3) for x in box]


def _center(box):
    return ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)


def _circles(paths):
    return [
        p
        for p in paths
        if 2 < p["rect"].width < 16
        and 2 < p["rect"].height < 16
        and abs(p["rect"].width - p["rect"].height) < 0.8
        and sum(item[0] == "c" for item in p["items"]) == 4
    ]


def _shape(circle, paths):
    box = circle["rect"]
    x, y = _center(box)
    for p in paths:
        cx, cy = _center(p["rect"])
        short_lines = [i for i in p["items"] if i[0] == "l" and abs(i[1] - i[2]) < 3]
        if (
            abs(cx - x) < 1.2
            and abs(cy - y) < 1.2
            and len(short_lines) >= 6
            and box.width < p["rect"].width < box.width * 2
        ):
            return "radial_circle"
    return "filled_circle" if circle.get("fill") is not None else "open_circle"


def _text_lines(page):
    return [
        {"text": "".join(s["text"] for s in line["spans"]), "box": pymupdf.Rect(line["bbox"])}
        for block in page.get_text("dict")["blocks"]
        if block["type"] == 0
        for line in block["lines"]
    ]


def recover_led_tables(page, paths=None):
    paths = paths if paths is not None else page.get_drawings()
    circles = _circles(paths)
    if not circles:
        return []
    lines = _text_lines(page)
    legend = {}
    for line in lines:
        text = re.sub(r"\s+", "", line["text"])
        state = (
            "闪烁"
            if "闪烁" in text
            else "常亮"
            if "常亮" in text
            else "熄灭"
            if "熄灭" in text
            else None
        )
        if state is None or "表示" not in text:
            continue
        bx = line["box"]
        candidates = [
            c
            for c in circles
            if abs(_center(c["rect"])[1] - _center(bx)[1]) < 6 and 0 <= bx.x0 - c["rect"].x1 < 20
        ]
        if len(candidates) == 1:
            c = candidates[0]
            legend[_shape(c, paths)] = {
                "state": state,
                "text": line["text"],
                "symbol_bbox": _box(c["rect"]),
                "text_bbox": _box(bx),
            }
    if {v["state"] for v in legend.values()} != {"常亮", "闪烁", "熄灭"}:
        return []
    headers = defaultdict(list)
    for word in page.get_text("words"):
        if re.fullmatch(r"LED\d+", word[4]):
            headers[round(word[1])].append(word)
    tables = []
    for words in headers.values():
        words = sorted(words, key=lambda w: w[0])
        if len(words) < 3:
            continue
        bottom = max(w[3] for w in words)
        ranges = [
            line
            for line in lines
            if _center(line["box"])[1] > bottom + 1
            and line["box"].y0 < bottom + page.rect.height * 0.4
            and "%" in line["text"]
            and "电量" in line["text"]
        ]
        ranges.sort(key=lambda line: line["box"].y0)
        rows, cells = [], []
        for line in ranges:
            row, row_cells = [], []
            y = _center(line["box"])[1]
            for word in words:
                x = (word[0] + word[2]) / 2
                candidates = [
                    c
                    for c in circles
                    if abs(_center(c["rect"])[0] - x) < (word[2] - word[0]) / 2
                    and abs(_center(c["rect"])[1] - y) < 5
                ]
                if len(candidates) != 1:
                    break
                circle = candidates[0]
                shape = _shape(circle, paths)
                if shape not in legend:
                    break
                row.append(legend[shape]["state"])
                row_cells.append(
                    {
                        "column": word[4],
                        "state": legend[shape]["state"],
                        "shape": shape,
                        "bbox": _box(circle["rect"]),
                    }
                )
            if len(row) != len(words):
                # Do not publish a partially guessed symbolic table.
                return []
            rows.append(row + [re.sub(r"\s+", "", line["text"])])
            cells.append(row_cells)
        if len(rows) < 2:
            continue
        bbox = pymupdf.Rect(
            min(w[0] for w in words) - 5,
            min(w[1] for w in words) - 3,
            max(line["box"].x1 for line in ranges) + 5,
            max(line["box"].y1 for line in ranges) + 3,
        )
        table = {
            "rows": [[w[4] for w in words] + ["电量"]] + rows,
            "cells": cells,
            "legend": legend,
            "method": "native_vector_symbols_with_legend",
            "bbox": _box(bbox),
            "table_kind": "led_battery_states",
        }
        tables.append(table)
    return tables
