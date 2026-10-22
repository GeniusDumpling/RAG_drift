"""百度搜索 → 网页原始证据 → 标准化文档；不执行入库或内容加工。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import requests
import trafilatura
import yaml
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SCRIPT_DIR = Path(__file__).resolve().parent
API_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
LOGGER = logging.getLogger("web_crawler")


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_config(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("配置文件必须是 YAML 对象")
    defaults = {
        "search": {
            "top_k": 20,
            "edition": "standard",
            "recency_filter": "none",
            "domain_filter": [],
            "timeout": 30,
        },
        "crawl": {"enabled": True, "timeout": 30, "retries": 2, "delay": 1.0},
        "output": {"directory": "./output"},
    }
    conf = {}
    for section, values in defaults.items():
        supplied = data.get(section, {})
        if not isinstance(supplied, dict):
            raise ValueError(f"{section} 必须是对象")
        conf[section] = values | supplied
    search = conf["search"]
    keywords = search.get("keywords")
    if (
        not isinstance(keywords, list)
        or not keywords
        or any(not isinstance(word, str) or not word.strip() for word in keywords)
    ):
        raise ValueError("search.keywords 必须是非空字符串列表")
    search["keywords"] = list(dict.fromkeys(word.strip() for word in keywords))
    if type(search["top_k"]) is not int or not 1 <= search["top_k"] <= 50:
        raise ValueError("search.top_k 必须是 1–50 的整数")
    if search["edition"] not in ("standard", "lite"):
        raise ValueError("search.edition 必须是 standard 或 lite")
    if search["recency_filter"] not in (None, "none", "week", "month", "semiyear", "year"):
        raise ValueError("search.recency_filter 不受支持")
    domains = search["domain_filter"]
    if (
        not isinstance(domains, list)
        or len(domains) > 20
        or any(not isinstance(domain, str) or not domain.strip() for domain in domains)
    ):
        raise ValueError("search.domain_filter 必须是最多 20 个站点的字符串列表")
    crawl = conf["crawl"]
    if type(crawl["enabled"]) is not bool:
        raise ValueError("crawl.enabled 必须是布尔值")
    if type(crawl["retries"]) is not int or crawl["retries"] < 0:
        raise ValueError("crawl.retries 必须是非负整数")
    for section, key in (("search", "timeout"), ("crawl", "timeout"), ("crawl", "delay")):
        value = conf[section][key]
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value < 0
            or (key == "timeout" and value == 0)
        ):
            raise ValueError(f"{section}.{key} 必须是有限{'正' if key == 'timeout' else '非负'}数")
    directory = conf["output"]["directory"]
    if not isinstance(directory, str) or not directory.strip():
        raise ValueError("output.directory 必须是非空路径")
    conf["output"]["directory"] = str((path.resolve().parent / directory).resolve())
    return conf


def search_payload(keyword: str, conf: dict) -> dict:
    payload = {
        "messages": [{"role": "user", "content": keyword}],
        "edition": conf["edition"],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [
            {"type": resource, "top_k": conf["top_k"] if resource == "web" else 0}
            for resource in ("web", "video", "image", "aladdin")
        ],
    }
    if conf["recency_filter"] not in (None, "none"):
        payload["search_recency_filter"] = conf["recency_filter"]
    if conf["domain_filter"]:
        payload["search_filter"] = {"match": {"site": conf["domain_filter"]}}
    return payload


def canonical_url(value: str) -> str:
    """仅去 fragment、统一协议/主机大小写和默认端口；保留路径和查询参数。"""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("仅支持无用户凭据的 HTTP(S) URL")
    port = parsed.port
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (parsed.scheme, port) not in (("http", 80), ("https", 443)):
        host += f":{port}"
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", parsed.query, ""))


def make_session(retries: int) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET", "POST")),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "RAG-WebCrawler/1.0"
    return session


def new_document(url: str, ref: dict, run_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "document_id": "web:" + digest(url),
        "canonical_url": url,
        "source_url": ref["url"],
        "final_url": None,
        "title": ref.get("title") or "",
        "author": None,
        "language": None,
        "published_at": None,
        "search_date": ref.get("date"),
        "discovered_at": now(),
        "fetched_at": None,
        "text": "",
        "text_sha256": None,
        "raw_sha256": None,
        "extraction_method": None,
        "extraction_version": None,
        "status": "pending",
        "error": None,
        "http_status": None,
        "content_type": None,
        "encoding": None,
        "raw_page_path": None,
        "search_matches": [],
    }


def fetch_document(session: requests.Session, document: dict, run_dir: Path, conf: dict) -> None:
    document["fetched_at"] = now()
    try:
        response = session.get(document["source_url"], timeout=conf["timeout"])
        document.update(
            final_url=response.url,
            http_status=response.status_code,
            content_type=response.headers.get("Content-Type", ""),
            encoding=response.encoding,
        )
        content_type = document["content_type"].split(";", 1)[0].strip().lower()
        is_html = content_type in ("text/html", "application/xhtml+xml", "")
        suffix = ".html" if is_html else ".bin"
        relative = f"raw/pages/{document['document_id'][4:]}{suffix}"
        (run_dir / relative).write_bytes(response.content)
        document.update(
            raw_page_path=relative, raw_sha256=hashlib.sha256(response.content).hexdigest()
        )
        response.raise_for_status()
        if not is_html and content_type != "text/plain":
            document.update(status="unsupported", error=f"不支持的正文类型：{content_type}")
            return
        if content_type == "text/plain":
            if "charset=" not in document["content_type"].lower():
                response.encoding = response.apparent_encoding or "utf-8"
            document["encoding"] = response.encoding
            text = response.text.strip()
            metadata = {}
            method = "plain_text"
        else:
            try:
                extracted = trafilatura.bare_extraction(
                    response.content,
                    url=response.url,
                    with_metadata=True,
                    include_comments=False,
                    include_tables=True,
                )
                metadata = extracted.as_dict() if extracted else {}
            except Exception as exc:
                document.update(status="failed", error=f"正文提取失败：{exc}")
                return
            text = (metadata.get("text") or "").strip()
            method = "trafilatura"
        if not text:
            document.update(status="empty", error="未提取到正文（可能需要 JS、登录或被拦截）")
            return
        document.update(
            status="success",
            text=text,
            text_sha256=digest(text),
            title=metadata.get("title") or document["title"],
            author=metadata.get("author"),
            language=metadata.get("language"),
            published_at=metadata.get("date"),
            extraction_method=method,
            extraction_version=trafilatura.__version__ if method == "trafilatura" else "1",
        )
    except (requests.RequestException, ValueError) as exc:
        document.update(status="failed", error=str(exc))


def run(conf: dict, api_key: str) -> Path:
    if not api_key:
        raise ValueError("请在环境变量或仓库根目录 .env 配置 BAIDU_QIANFAN_API_KEY")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    run_dir = Path(conf["output"]["directory"]) / run_id
    (run_dir / "raw/search").mkdir(parents=True)
    (run_dir / "raw/pages").mkdir()
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "started_at": now(),
        "finished_at": None,
        "status": "running",
        "config": conf,
        "documents_path": "documents.jsonl",
        "searches": [],
        "counts": {},
    }
    write_json(run_dir / "manifest.json", manifest)
    documents = {}
    with make_session(conf["crawl"]["retries"]) as session:
        for index, keyword in enumerate(conf["search"]["keywords"], 1):
            LOGGER.info("搜索 %s", keyword)
            payload = search_payload(keyword, conf["search"])
            raw_path = f"raw/search/{index:03d}.json"
            search = {
                "keyword": keyword,
                "request": payload,
                "searched_at": now(),
                "raw_response_path": None,
                "status": "pending",
                "error": None,
                "returned": 0,
                "selected": 0,
                "invalid": 0,
            }
            manifest["searches"].append(search)
            try:
                # Authorization 仅附加在搜索请求上，不传给网页站点。
                response = session.post(
                    API_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=conf["search"]["timeout"],
                )
                # 完整保存响应体，包括错误响应；不修改 references 的 source 等字段。
                (run_dir / raw_path).write_bytes(response.content)
                search["raw_response_path"] = raw_path
                search["http_status"] = response.status_code
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict) or not isinstance(result.get("references"), list):
                    raise ValueError("百度响应缺少 references 数组，请检查原始响应")
                refs = result["references"]
                search["returned"] = len(refs)
                selected = 0
                for rank, ref in enumerate(refs, 1):
                    if not isinstance(ref, dict) or ref.get("type", "web") != "web":
                        search["invalid"] += 1
                        continue
                    try:
                        url = canonical_url(ref.get("url") or "")
                    except (ValueError, AttributeError):
                        search["invalid"] += 1
                        continue
                    if selected >= conf["search"]["top_k"]:
                        break
                    selected += 1
                    if url not in documents:
                        documents[url] = new_document(url, ref, run_id)
                    documents[url]["search_matches"].append(
                        {
                            "keyword": keyword,
                            "rank": rank,
                            "snippet": ref.get("content") or "",
                            "search_date": ref.get("date"),
                            "raw_response_path": raw_path,
                            "reference_pointer": f"/references/{rank - 1}",
                        }
                    )
                search.update(status="success", selected=selected)
            except (requests.RequestException, ValueError) as exc:
                search.update(status="failed", error=str(exc))
                LOGGER.error("搜索失败 %s：%s", keyword, exc)
            write_json(run_dir / "manifest.json", manifest)

        counts = Counter()
        with (run_dir / "documents.jsonl").open("w", encoding="utf-8") as output:
            for index, document in enumerate(documents.values()):
                if conf["crawl"]["enabled"]:
                    if index:
                        time.sleep(conf["crawl"]["delay"])
                    fetch_document(session, document, run_dir, conf["crawl"])
                else:
                    document["status"] = "search_only"
                output.write(json.dumps(document, ensure_ascii=False) + "\n")
                output.flush()
                counts[document["status"]] += 1
                manifest["counts"] = dict(counts)
                write_json(run_dir / "manifest.json", manifest)
                LOGGER.info("[%s] %s", document["status"], document["source_url"])

    failed = any(search["status"] == "failed" for search in manifest["searches"])
    failed = failed or any(counts[status] for status in ("failed", "empty", "unsupported"))
    manifest.update(status="partial" if failed else "success", finished_at=now())
    write_json(run_dir / "manifest.json", manifest)
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=SCRIPT_DIR / "conf.yaml")
    parser.add_argument("--dry-run", action="store_true", help="校验配置并显示请求，不联网")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        conf = load_config(args.config)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "config": conf,
                        "requests": [
                            search_payload(keyword, conf["search"])
                            for keyword in conf["search"]["keywords"]
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        load_dotenv(SCRIPT_DIR.parents[1] / ".env", override=False)
        run_dir = run(conf, os.environ.get("BAIDU_QIANFAN_API_KEY", "").strip())
        LOGGER.info("产物目录：%s", run_dir)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        return 0 if manifest["status"] == "success" else 2
    except (OSError, ValueError, yaml.YAMLError) as exc:
        LOGGER.error("运行失败：%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
