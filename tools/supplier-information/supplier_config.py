"""Shared brand context for the standalone supplier commands (DJI is the default)."""

import argparse
import copy
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
_brand_id = None
_config_path = ROOT / "conf.yaml"
_overrides = {}


def configure(brand=None, config_path=None, overrides=None):
    global _brand_id, _config_path, _overrides
    _brand_id = brand
    _config_path = Path(config_path).resolve() if config_path else ROOT / "conf.yaml"
    _overrides = overrides or {}


def load_config():
    with _config_path.open(encoding="utf-8") as handle:
        conf = yaml.safe_load(handle) or {}
    brand_id = _brand_id or conf.get("brand", "dji")
    brands = conf.get("brands", {})
    if brand_id != "dji" and brand_id not in brands:
        raise ValueError(f"Unknown brand: {brand_id}; configure it in brands first")
    profile = {
        "id": brand_id,
        "name": "大疆",
        "aliases": ["大疆", "DJI"],
        "language": "zh",
        **brands.get(brand_id, {}),
    }
    profile["id"] = brand_id
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", brand_id):
        raise ValueError("Brand id must contain lowercase letters, numbers, _ or -")
    conf = copy.deepcopy(conf)
    for section in ("search", "verify", "llm"):
        conf[section] = {
            **conf.get(section, {}),
            **profile.get(section, {}),
            **_overrides.get(section, {}),
        }
    if brand_id != "dji":
        conf["search"].setdefault("provider", "auto")
        # A DJI-specific search instruction must never leak to another brand.
        if "instruction" not in profile.get("search", {}):
            conf["search"]["instruction"] = (
                f"Find original evidence of suppliers and components used by {profile['name']}."
            )
    conf["brand_profile"] = profile
    return conf


def brand():
    return load_config()["brand_profile"]


def index_dir():
    profile = brand()
    if profile["id"] == "dji":
        return ROOT / "search_results" / "_index"
    return ROOT / "search_results" / profile["id"] / "_index"


def confirmed_path():
    return str(index_dir() / "confirmed_relations.md")


def matches_buyer(value):
    # Token boundaries avoid matching e.g. "Parrot distributor" as the buyer itself.
    normalized = re.sub(r"[^\w]", "", value.casefold())
    return normalized in {
        re.sub(r"[^\w]", "", name.casefold())
        for name in [brand()["name"], *brand().get("aliases", [])]
    }


def cli(description, verification=False):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--brand", help="Brand id from conf.yaml: dji, parrot, skydio")
    parser.add_argument("--config", help="Alternate YAML configuration path")
    parser.add_argument("--provider", choices=["auto", "baidu", "brave"])
    parser.add_argument("--max-results", type=int, help="Maximum source documents for this run")
    parser.add_argument("--force", action="store_true", help="Reprocess known URLs")
    if verification:
        parser.add_argument("--mode", choices=["unverified", "failed", "all"])
        parser.add_argument("--max-suppliers", type=int, help="0 means all matching suppliers")
        parser.add_argument("--fetch-limit", type=int)
    args = parser.parse_args()
    for field in ("max_results", "max_suppliers", "fetch_limit"):
        value = getattr(args, field, None)
        if value is not None and (value < 0 or (field != "max_suppliers" and value == 0)):
            parser.error(f"--{field.replace('_', '-')} has an invalid value")
    overrides = {"search": {}, "verify": {}}
    if args.provider:
        overrides["search"]["provider"] = args.provider
    if args.max_results:
        overrides["search"]["max_results"] = args.max_results
    if args.force:
        overrides["search"]["incremental"] = False
    if verification:
        for field in ("mode", "max_suppliers", "fetch_limit"):
            if getattr(args, field) is not None:
                overrides["verify"][field] = getattr(args, field)
    configure(args.brand, args.config, overrides)
    return args
