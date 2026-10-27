import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[2]


def uid(*parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, "drone-documents:" + ":".join(map(str, parts))))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def model_name(root: Path, manifest: dict) -> str:
    if manifest.get("model_name"):
        return manifest["model_name"]
    return next(
        (
            r["name"]
            for r in read_jsonl(root / "models.jsonl")
            if r["slug"] == manifest["model_slug"]
        ),
        manifest["model_slug"],
    )


def progress(event: str, **details) -> None:
    print(json.dumps({"event": event, **details}, ensure_ascii=False), flush=True)


class Assets:
    def __init__(self, root: Path):
        self.root = root
        self.rows: dict[str, dict] = {}

    def add(self, data: bytes, suffix: str, mime: str, **details) -> str:
        sha = digest(data)
        asset_id = uid("asset", sha)
        key = f"assets/{sha[:2]}/{sha}{suffix}"
        path = self.root / key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.rows[asset_id] = {
            "id": asset_id,
            "sha256": sha,
            "storage_key": key,
            "mime_type": mime,
            "bytes": len(data),
            **details,
        }
        return asset_id


def seal_bundle(root: Path, manifest: dict) -> None:
    manifest["files"] = {f.name: digest(f.read_bytes()) for f in sorted(root.glob("*.jsonl"))}
    manifest["updated_at"] = now()
    write_json(root / "manifest.json", manifest)


def validate_bundle(root: Path) -> dict:
    manifest = read_json(root / "manifest.json")
    for name, sha in manifest["files"].items():
        if digest((root / name).read_bytes()) != sha:
            raise ValueError(f"数据包校验失败: {name}")
    assets = {r["id"]: r for r in read_jsonl(root / "assets.jsonl")}
    for asset in assets.values():
        path = (root / asset["storage_key"]).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path.read_bytes()) != asset["sha256"]:
            raise ValueError(f"资源校验失败: {asset['id']}")
    elements = {e["id"] for e in read_jsonl(root / "elements.jsonl")}
    pages = {p["id"] for p in read_jsonl(root / "pages.jsonl")}
    for element in read_jsonl(root / "elements.jsonl"):
        for region in element["regions"]:
            if region["page_id"] not in pages:
                raise ValueError("元素引用了不存在的页面")
        if element.get("asset_id") and element["asset_id"] not in assets:
            raise ValueError("元素引用了不存在的文件")
        if not set(element["metadata_json"].get("footnote_ids", [])) <= elements:
            raise ValueError("参数引用了不存在的脚注")
    for chunk in read_jsonl(root / "chunks.jsonl"):
        if not set(chunk["element_ids"]) <= elements:
            raise ValueError("切片引用了不存在的元素")
        if not chunk["display_text"].strip() or not chunk["embed_text"].strip():
            raise ValueError("存在空切片")
        if chunk["chunk_type"] == "figure_description" and not chunk["metadata_json"].get(
            "index_eligible", True
        ):
            raise ValueError("隔离的图片说明不能进入索引")
    return manifest
