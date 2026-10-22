import csv
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.schemas.drones import (
    DroneCatalog,
    DroneDetail,
    DroneParameter,
    DroneParameterGroup,
    DroneSummary,
)


class DroneCatalogUnavailable(Exception):
    pass


def _category(slug: str) -> str:
    if slug.startswith("matrice") or slug in {
        "mavic-2-enterprise", "mavic-2-enterprise-advanced", "mavic-3-enterprise",
        "mavic-3-m", "phantom-4-rtk", "p4-multispectral",
    }:
        return "行业应用"
    if slug.startswith(("mg-", "t")):
        return "农业植保"
    if slug.startswith("inspire"):
        return "专业影视"
    if slug.startswith("flycart"):
        return "运载运输"
    if slug.startswith("robomaster"):
        return "教育开发"
    return "消费航拍"


def parse_parameter_groups(html: str) -> list[DroneParameterGroup]:
    """Read both current support-page markup and the legacy Inspire specs table.

    Render text only, keeping line breaks, units and test-condition footnotes.
    h3 headings distinguish variants such as M200 / M210 / M210 RTK.
    """
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    groups: list[DroneParameterGroup] = []
    variant = ""
    group: DroneParameterGroup | None = None
    for node in soup.find_all(["h2", "h3", "strong"]):
        label = node.get_text(" ", strip=True)
        if node.name == "h3":
            variant = label
            group = None
        elif node.name == "h2":
            if label == "技术参数":
                continue
            group = DroneParameterGroup(
                name=" / ".join(filter(None, [variant, label])) or "其他参数",
                parameters=[],
            )
            groups.append(group)
        else:
            # Modern pages wrap each label and value together in a div. Legacy
            # pages put the label in h5, two levels below the parameter row.
            container = node.parent
            if isinstance(container, Tag) and container.name == "h5":
                label_container = container.parent
                container = label_container.parent if label_container is not None else None
            if not isinstance(container, Tag):
                continue
            text = container.get_text("\n", strip=True)
            value = text.removeprefix(node.get_text("\n", strip=True)).strip()
            if group is None:
                group = DroneParameterGroup(name=variant or "技术参数", parameters=[])
                groups.append(group)
            group.parameters.append(DroneParameter(name=label or "说明", value=value))
    return [group for group in groups if group.parameters]


class DroneCatalogService:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _rows(self) -> list[dict[str, str]]:
        try:
            with (self.root / "技术参数索引.csv").open(encoding="utf-8-sig", newline="") as f:
                return list(csv.DictReader(f))
        except (OSError, UnicodeError, csv.Error) as exc:
            raise DroneCatalogUnavailable("无人机资料暂不可用，请检查 DJI 文档目录配置。") from exc

    def _summary(self, row: dict[str, str]) -> DroneSummary:
        try:
            return DroneSummary(
                slug=row["slug"], name=row["name"], category=_category(row["slug"]),
                status=row["status"], group_count=int(row["group_count"]),
                parameter_count=int(row["parameter_count"]), source_url=row["source_url"],
                collected_at=row["collected_at"], note=row.get("error", ""),
            )
        except (KeyError, ValueError) as exc:
            raise DroneCatalogUnavailable("无人机技术参数索引格式有误。") from exc

    def catalog(self) -> DroneCatalog:
        items = sorted(
            (self._summary(row) for row in self._rows()), key=lambda x: x.name.casefold(),
        )
        return DroneCatalog(
            items=items, total=len(items), parameter_count=sum(x.parameter_count for x in items),
            partial_count=sum(x.status == "partial" for x in items),
        )

    def detail(self, slug: str) -> DroneDetail | None:
        row = next((row for row in self._rows() if row.get("slug") == slug), None)
        if row is None:
            return None
        summary = self._summary(row)
        try:
            path = (self.root / row["html_path"].replace("\\", "/")).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError("Path outside catalog")
            groups = parse_parameter_groups(path.read_text(encoding="utf-8-sig"))
        except (KeyError, OSError, UnicodeError, ValueError) as exc:
            raise DroneCatalogUnavailable("该型号的技术参数文件暂不可用。") from exc
        return DroneDetail(
            **summary.model_dump(exclude={"group_count", "parameter_count"}), groups=groups,
            group_count=len(groups), parameter_count=sum(len(g.parameters) for g in groups),
        )
