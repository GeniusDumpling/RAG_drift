#!/usr/bin/env python3
"""Manage the unified graph and replay reviewed video builds without model calls."""

import argparse
import copy
import importlib.util
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.core.config import Settings  # noqa: E402
from app.services.document_graph.source import save_json  # noqa: E402
from app.services.document_graph.storage import DocumentGraphStore  # noqa: E402
from app.services.knowledge_graph import queries  # noqa: E402
from app.services.knowledge_graph.storage import UnifiedGraphStore  # noqa: E402
from app.services.video_graph.material import load_material  # noqa: E402
from app.services.video_graph.pipeline import verify_projection  # noqa: E402
from app.services.video_graph.storage import GraphStore  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def source_settings(settings, path):
    spec = importlib.util.spec_from_file_location(
        "video_graph_target", ROOT / "tools/video-knowledge-graph/build_graph.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.target_settings(settings, path)


def replay_video(settings, args):
    incoming = source_settings(settings, args.source_env_file)
    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    report = dict(status="running", videos=[], model_http_calls=0)
    try:
        with GraphStore(incoming) as source, GraphStore(settings) as target:
            target.initialize()
            for cid in args.content_id:
                with Session(engine) as session:
                    material = load_material(session, cid)
                if material is None:
                    raise ValueError("PostgreSQL 中找不到原始视频材料")
                snapshot = source.active_snapshot(cid)
                if snapshot.get("status") != "success" or snapshot.get("withdrawn"):
                    raise ValueError("源视频没有完整有效构建")
                check = verify_projection(
                    engine, cid, snapshot["version"], material.fingerprint, store=source
                )
                if not check["verified"]:
                    raise ValueError("源视频图谱与当前业务材料或审核结果不符")
                # Replay through the same checkpoint/activation entry as the normal
                # worker. Native and shared projections commit in one transaction.
                previous = target.active_snapshot(cid)
                if (
                    previous.get("version") == snapshot["version"]
                    and previous.get("fingerprint") == material.fingerprint
                    and not previous.get("withdrawn")
                ):
                    target.refresh_shared(material)
                    verification = verify_projection(
                        engine, cid, snapshot["version"], material.fingerprint, store=target
                    )
                    if not verification["verified"]:
                        raise ValueError("重投影回读校验失败")
                    status = "already_current"
                else:
                    target.enqueue(material, snapshot["version"], retry_failed=True)
                    claimed = target.claim(snapshot["version"], [cid])
                    if claimed is None:
                        raise ValueError("目标任务无法领取，可能有工作者正在处理或来源已撤回")
                    build, token = claimed
                    output = copy.deepcopy(snapshot["output"])
                    target.checkpoint(build["id"], material.id, token, output)
                    status = target.activate(material, build["id"], token, output)
                    verification = verify_projection(
                        engine, cid, snapshot["version"], material.fingerprint, store=target
                    )
                    if status != "success" or not verification["verified"]:
                        raise ValueError("重放入库或统一投影校验失败")
                row = dict(
                    content_id=str(cid),
                    title=material.title,
                    status=status,
                    source_build_id=snapshot["id"],
                    source_verification=check,
                    verification=verification,
                )
                report["videos"].append(row)
                save_json(args.report, report)
    finally:
        engine.dispose()
    report["status"] = "success"
    save_json(args.report, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("sources")
    verify = sub.add_parser("verify")
    verify.add_argument("--source-id", required=True)
    query = sub.add_parser("query")
    query.add_argument("--q", default="")
    query.add_argument("--source-type")
    query.add_argument("--entity-id")
    query.add_argument("--limit", type=int, default=20)
    query.add_argument("--offset", type=int, default=0)
    replay = sub.add_parser("replay-video")
    replay.add_argument("--source-env-file", type=Path, required=True)
    replay.add_argument("--content-id", type=uuid.UUID, action="append", required=True)
    replay.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.env_file.is_file():
        raise ValueError("统一图谱配置文件不存在，拒绝回退")
    settings = Settings(_env_file=(ROOT / ".env", args.env_file))
    if not settings.unified_graph_enabled:
        raise ValueError("目标配置须明确启用 UNIFIED_GRAPH_ENABLED")
    if args.command == "replay-video":
        result = replay_video(settings, args)
    elif args.command == "init":
        with GraphStore(settings) as video, DocumentGraphStore(settings) as documents:
            video.initialize()
            documents.initialize()
        result = dict(status="initialized")
    else:
        with UnifiedGraphStore(settings) as store:
            if args.command == "sources":
                result = dict(items=queries.sources(store))
            elif args.command == "verify":
                result = queries.verify(store, args.source_id)
            else:
                result = queries.retrieve(
                    store,
                    args.q,
                    source_type=args.source_type,
                    entity_id=args.entity_id,
                    limit=args.limit,
                    offset=args.offset,
                )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result.get("verified") is False)


if __name__ == "__main__":
    raise SystemExit(main())
