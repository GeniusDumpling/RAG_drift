"""配置加载：conf.yaml + 全局 .env。

约定：
- 非敏感参数统一放在本目录 conf.yaml；
- API Key 等敏感值统一从仓库根目录 .env（全局）读取，脚本不再各自解析 .env。
"""

from __future__ import annotations

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
ENV_PATH = REPO_ROOT / ".env"
DEFAULT_CONF_PATH = SCRIPT_DIR / "conf.yaml"


def load_global_env(env_path: Path = ENV_PATH) -> None:
    """把仓库根 .env 载入进程环境；已有环境变量优先，不覆盖。"""
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)


def env(key: str, default: str = "") -> str:
    """读取环境变量（strip 后），未设置时返回 default。"""
    return os.environ.get(key, default).strip()


def load_conf(path: Path = DEFAULT_CONF_PATH) -> dict:
    """读取 conf.yaml 并返回 dict。"""
    import yaml

    if not path.is_file():
        raise FileNotFoundError(f"缺少配置文件: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误: {path}")
    return data


def conf_get(conf: dict, section: str, key: str, default=None):
    """安全读取嵌套配置项：conf[section].get(key, default)。"""
    if not isinstance(conf.get(section), dict):
        return default
    return conf[section].get(key, default)
