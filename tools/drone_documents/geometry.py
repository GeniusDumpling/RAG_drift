"""Lightweight PDF graphic clustering, shared by document parsing and graph preparation."""

import pymupdf


def _merge_boxes(boxes: list, page_rect) -> list:
    merged = []
    for box in boxes:
        box = pymupdf.Rect(box) & page_rect
        if box.width < 25 or box.height < 22 or box.get_area() < 900:
            continue
        for previous in merged:
            intersection = (previous & box).get_area()
            if intersection / max(1, min(previous.get_area(), box.get_area())) > 0.2:
                previous |= box
                break
        else:
            merged.append(box)
    return sorted(merged, key=lambda b: b.get_area(), reverse=True)[:8]
