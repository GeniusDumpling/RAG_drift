"""Discover models and shared documents from the collected DJI indexes."""

import csv
from collections import defaultdict
from pathlib import Path


def indexed_path(root: Path, relative: str) -> Path:
    path = (root / relative.replace("\\", "/")).resolve()
    if not relative or not path.is_relative_to(root.resolve()):
        raise ValueError("索引文件路径为空或越界")
    return path


def read_index(root: Path, name: str) -> list[dict]:
    path = root / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


class Catalog:
    def __init__(self, root: Path):
        self.root = root
        self.documents = read_index(root, "文档索引.csv")
        self.specifications = read_index(root, "技术参数索引.csv")
        if not self.documents and not self.specifications:
            raise ValueError(f"目录中没有可用的文档索引或技术参数索引: {root}")
        self.models = {}
        self.by_hash = defaultdict(list)
        for row in self.documents + self.specifications:
            self.models[row["slug"]] = {"slug": row["slug"], "name": row["name"]}
        for row in self.documents:
            if row.get("sha256") and row.get("local_path"):
                self.by_hash[row["sha256"]].append(row)

    def model(self, slug: str) -> dict:
        if slug not in self.models:
            raise ValueError(f"未找到型号: {slug}；可先运行 list-models 查看索引。")
        return self.models[slug]

    def sources(self, slug: str) -> list[dict]:
        self.model(slug)
        # Every model uses the same canonical record for a shared source hash.
        # Titles/paths therefore do not change with the order of model imports.
        return [rows[0] for rows in self.by_hash.values() if any(r["slug"] == slug for r in rows)]

    def spec(self, slug: str) -> dict | None:
        return next((r for r in self.specifications if r["slug"] == slug), None)

    def summaries(self) -> list[dict]:
        return [
            {
                **self.models[slug],
                "pdf_count": len(self.sources(slug)),
                "specification_status": (self.spec(slug) or {}).get("status", "missing"),
            }
            for slug in sorted(self.models)
        ]
