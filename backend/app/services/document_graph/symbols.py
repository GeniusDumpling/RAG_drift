"""Conservative LED symbol measurements from original PDF vectors or embedded pixels.

No model-specific fact answers, battery ranges or row patterns are encoded here.
Only recognizable filled/empty circles, radial blink strokes, green lamps and red
crossed-out lamps are decoded. Unknown glyphs retain the normal visual-review path.
"""

import hashlib
import re

from .source import case_source, checked_path


def center(rect):
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def normalized_rect(rect, page):
    return [
        round(rect[0] / page.rect.width * 1000, 2),
        round(rect[1] / page.rect.height * 1000, 2),
        round(rect[2] / page.rect.width * 1000, 2),
        round(rect[3] / page.rect.height * 1000, 2),
    ]


def raster_symbol(pix):
    """Decode only the colored lamp family; return None for unfamiliar images."""
    if pix.n < 3:
        return None
    raw = pix.samples
    green = set()
    red = 0
    for i in range(pix.width * pix.height):
        r, g, b = raw[i * pix.n : i * pix.n + 3]
        if pix.alpha and raw[i * pix.n + pix.n - 1] < 32:
            continue
        if g > 100 and g > r * 1.25 and g > b * 1.25:
            green.add(i)
        if r > 100 and r > g * 1.3 and r > b * 1.3:
            red += 1
    if green and red >= 3:
        return None
    if not green:
        return ("off", dict(green_pixels=0, red_pixels=red)) if red >= 3 else None
    components = []
    while green:
        todo = [green.pop()]
        count = 0
        while todo:
            p = todo.pop()
            count += 1
            x, y = p % pix.width, p // pix.width
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                q = ny * pix.width + nx
                if 0 <= nx < pix.width and 0 <= ny < pix.height and q in green:
                    green.remove(q)
                    todo.append(q)
        if count >= 2:
            components.append(count)
    if not components or max(components) < 12:
        return None
    # A steady green lamp is one connected disc/ring; blinking has detached rays.
    if len(components) == 1:
        state = "steady"
    elif len(components) >= 5:
        state = "blinking"
    else:
        return None
    return state, dict(green_components=sorted(components, reverse=True), red_pixels=red)


def vector_symbol(drawing, drawings):
    """Decode the grey-circle family, whose radial strokes explicitly mean blinking."""
    fill = drawing.get("fill")
    rect = drawing["rect"]
    if fill is None and drawing.get("color") is not None:
        return "off", dict(fill=None, radial_strokes=0)
    if not fill or len(fill) != 3 or max(fill) - min(fill) > 0.06:
        return None
    if min(fill) > 0.97:
        return "off", dict(fill=list(fill), radial_strokes=0)
    if not 0.55 < sum(fill) / 3 < 0.95:
        return None
    x, y = center(rect)
    radial = [
        d
        for d in drawings
        if d.get("fill") is None
        and len(d["items"]) >= 8
        and all(i[0] == "l" for i in d["items"])
        and abs(center(d["rect"])[0] - x) < 0.5
        and abs(center(d["rect"])[1] - y) < 0.5
        and 1.2 < d["rect"].width / rect.width < 2.5
    ]
    count = max((len(d["items"]) for d in radial), default=0)
    return ("blinking" if count else "steady"), dict(fill=list(fill), radial_strokes=count)


def led_measurements(bundle, case, root):
    if case["kind"] != "led_states":
        return []
    import pymupdf

    manual, pages = case_source(bundle, case)
    asset = bundle["assets"][manual["pdf_asset_id"]]
    output = []
    with pymupdf.open(checked_path(root, asset["storage_key"])) as pdf:
        for material in pages:
            page = pdf[material["number"] - 1]
            drawings = page.get_drawings()
            images = page.get_image_info(xrefs=True)
            circle_paths = [
                d
                for d in drawings
                if len(d["items"]) == 4
                and all(i[0] == "c" for i in d["items"])
                and 2 < d["rect"].width < 18
                and 0.85 < d["rect"].width / max(0.01, d["rect"].height) < 1.15
            ]
            raster_cache = {}
            for region in material["regions"]:
                box = region["bbox"]

                def within(r, box=box):
                    return box[0] <= center(r)[0] <= box[2] and box[1] <= center(r)[1] <= box[3]

                heads = [
                    r
                    for r in material["lines"]
                    if re.fullmatch(r"LED\s*[1-4]", r["text"].strip(), re.I) and within(r["bbox"])
                ]
                if len(heads) != 4:
                    continue
                heads.sort(key=lambda r: int(re.search(r"[1-4]", r["text"])[0]))
                if (
                    max(center(h["bbox"])[1] for h in heads)
                    - min(center(h["bbox"])[1] for h in heads)
                    > 8
                ):
                    continue
                columns = [center(h["bbox"])[0] for h in heads]
                if columns != sorted(columns):
                    continue
                y_header = sum(center(h["bbox"])[1] for h in heads) / 4
                rows = [
                    r
                    for r in material["lines"]
                    if within(r["bbox"])
                    and center(r["bbox"])[1] > y_header
                    and ("%" in r["text"] or "fully charged" in r["text"].casefold())
                ]
                for row in rows:
                    y = center(row["bbox"])[1]
                    tolerance = (row["bbox"][3] - row["bbox"][1]) * 0.7
                    cells = []
                    for x in columns:
                        candidates = []
                        for d in circle_paths:
                            r = normalized_rect(d["rect"], page)
                            cx, cy = center(r)
                            if abs(cx - x) < 18 and abs(cy - y) < tolerance:
                                found = vector_symbol(d, drawings)
                                if found:
                                    candidates.append(
                                        (abs(cx - x) + abs(cy - y) * 2, found, r, "vector")
                                    )
                        for image in images:
                            r = normalized_rect(image["bbox"], page)
                            cx, cy = center(r)
                            if (
                                r[2] - r[0]
                                >= min(b - a for a, b in zip(columns, columns[1:], strict=False))
                                * 0.8
                                or r[3] - r[1] >= tolerance * 5
                            ):
                                continue
                            if (
                                abs(cx - x) >= 18
                                or abs(cy - y) >= tolerance
                                or not image.get("xref")
                            ):
                                continue
                            xref = image["xref"]
                            if xref not in raster_cache:
                                pix = pymupdf.Pixmap(pdf, xref)
                                found = raster_symbol(pix)
                                if found:
                                    found = (
                                        found[0],
                                        {
                                            **found[1],
                                            "pixel_sha256": hashlib.sha256(pix.samples).hexdigest(),
                                            "pixel_width": pix.width,
                                            "pixel_height": pix.height,
                                        },
                                    )
                                raster_cache[xref] = found
                            found = raster_cache[xref]
                            if found:
                                candidates.append(
                                    (abs(cx - x) + abs(cy - y) * 2, found, r, "raster")
                                )
                        if not candidates:
                            break
                        _, (state, features), rect, method = min(candidates, key=lambda v: v[0])
                        cells.append(dict(state=state, bbox=rect, method=method, features=features))
                    if len(cells) == 4:
                        output.append(
                            dict(
                                page=material["number"],
                                region_id=region["id"].rsplit(":", 1)[-1],
                                value_line_id=row["id"].rsplit(":", 1)[-1],
                                value=row["text"],
                                led_pattern=[c["state"] for c in cells],
                                cells=cells,
                                document_sha256=manual["sha256"],
                            )
                        )
    return output
