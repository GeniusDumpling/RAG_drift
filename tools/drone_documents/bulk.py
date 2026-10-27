"""Durable, document-deduplicated batch ingestion. Each stage runs in a child process."""

import asyncio
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf
from app.core.config import get_settings

from . import PIPELINE_VERSION
from .catalog import Catalog, indexed_path
from .common import ROOT, digest, now, progress, read_json, write_json

STAGES = ("prepare", "enrich", "index")


def start_batch(output: Path, parse_workers=2, enrich_workers=2, concurrency=3) -> dict:
    from contextlib import closing

    from qdrant_client import QdrantClient

    from .storage import bootstrap, configuration, ensure_collection

    if min(parse_workers, enrich_workers, concurrency) < 1:
        raise ValueError("批处理并发参数必须为正整数")
    plan = read_json(output / "plan.json")
    create_plan(
        Path(plan["configuration"]["input_root"]),
        output,
        models=plan["configuration"]["models"],
        exclude=(),
    )
    output.mkdir(parents=True, exist_ok=True)
    with (output / "batch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("该批次已经在运行") from exc
    bootstrap()
    _, url, collection = configuration()
    with closing(QdrantClient(url=url, timeout=20)) as client:
        ensure_collection(client, collection)
    with (output / "coordinator.log").open("a") as log:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.drone_documents",
                "batch-run",
                "--batch-root",
                str(output.resolve()),
                "--parse-workers",
                str(parse_workers),
                "--enrich-workers",
                str(enrich_workers),
                "--concurrency",
                str(concurrency),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    result = {
        "pid": child.pid,
        "batch_root": str(output.resolve()),
        "started_at": now(),
        "parse_workers": parse_workers,
        "enrich_workers": enrich_workers,
        "max_parallel_vision_requests": enrich_workers * concurrency,
    }
    write_json(output / "launcher.json", result)
    return result


def create_plan(input_root: Path, output: Path, models=None, exclude=("air-2s",)) -> dict:
    catalog = Catalog(input_root)
    selected = sorted(set(models or catalog.models) - set(exclude))
    for slug in selected:
        catalog.model(slug)
    if not selected:
        raise ValueError("没有待处理型号")
    snapshot = {
        name: digest((input_root / name).read_bytes())
        for name in ["文档索引.csv", "技术参数索引.csv"]
        if (input_root / name).exists()
    }
    settings = get_settings()
    config = {
        "pipeline_version": PIPELINE_VERSION,
        "indexes": snapshot,
        "vlm_model": settings.vlm_model,
        "embedding_model": settings.embedding_model,
        "input_root": str(input_root.resolve()),
        "models": selected,
    }
    if (output / "plan.json").exists():
        previous = read_json(output / "plan.json")
        if previous["configuration"] != config:
            raise ValueError("索引、模型配置或处理版本已变化，请使用新的批处理输出目录")
        return previous
    output.mkdir(parents=True, exist_ok=True)
    jobs = []
    # Specifications are small and become searchable early while the PDF queues run.
    for slug in selected:
        row = catalog.spec(slug)
        if row and row.get("html_path"):
            source = indexed_path(input_root, row["html_path"])
            if row.get("sha256") and digest(source.read_bytes()) != row["sha256"]:
                raise ValueError(f"参数文件哈希不一致: {slug}")
            jobs.append(
                {
                    "id": "spec-" + slug,
                    "kind": "spec",
                    "model": slug,
                    "models": [slug],
                    "title": row["name"] + " 技术参数",
                    "pages": 0,
                }
            )
    pdfs = []
    for sha, associations in catalog.by_hash.items():
        slugs = sorted({r["slug"] for r in associations})
        if not set(slugs).intersection(selected):
            continue
        row = associations[0]
        path = indexed_path(input_root, row["local_path"])
        if digest(path.read_bytes()) != sha:
            raise ValueError(f"PDF 哈希不一致: {row['title']}")
        with pymupdf.open(path) as doc:
            pages = len(doc)
        pdfs.append(
            {
                "id": "pdf-" + sha,
                "kind": "pdf",
                "model": row["slug"],
                "models": slugs,
                "sha256": sha,
                "title": row["title"],
                "pages": pages,
            }
        )
    jobs.extend(sorted(pdfs, key=lambda j: (j["pages"], j["id"])))
    plan = {
        "created_at": now(),
        "configuration": config,
        "model_names": {s: catalog.models[s]["name"] for s in selected},
        "jobs": jobs,
        "unique_pdfs": len(pdfs),
        "source_pages": sum(j["pages"] for j in pdfs),
    }
    write_json(output / "plan.json", plan)
    shutil.copyfile(Path(__file__).with_name("batch_preview.html"), output / "index.html")
    for job in jobs:
        write_json(
            output / "jobs" / (job["id"] + ".json"),
            {
                "id": job["id"],
                "stages": {},
                "attempts": {},
                "state": "pending",
                "updated_at": now(),
            },
        )
    return plan


def seed_cache(output: Path, existing: Path) -> None:
    for directory in ["vlm-cache", "embedding-cache"]:
        target = output / "cache" / directory
        target.mkdir(parents=True, exist_ok=True)
        if not (existing / directory).exists():
            continue
        for path in (existing / directory).glob("*.json"):
            destination = target / path.name
            if not destination.exists():
                # Cached successes are immutable; new responses use distinct atomic writes.
                shutil.copyfile(path, destination)


def bundle_path(output: Path, job: dict) -> Path:
    return output / "documents" / job["id"]


def stage_worker(output: Path, job_id: str, stage: str, concurrency=3) -> int:
    from .common import validate_bundle
    from .enrich import enrich
    from .extract import prepare
    from .report import report
    from .storage import import_bundle, index_bundle

    plan = read_json(output / "plan.json")
    os.environ["DOCUMENT_MODEL_RATE_DIR"] = str((output / "rate_limits").resolve())
    job = next(j for j in plan["jobs"] if j["id"] == job_id)
    bundle = bundle_path(output, job)
    result_path = output / "results" / f"{job_id}-{stage}.json"
    try:
        if stage == "prepare":
            if bundle.exists():
                try:
                    validate_bundle(bundle)
                    write_json(result_path, {"ok": True, "reused": True})
                    return 0
                except Exception:
                    bundle.rename(bundle.with_name(bundle.name + ".failed-" + str(time.time_ns())))
            temporary = bundle.with_name(bundle.name + ".preparing")
            if temporary.exists():
                temporary.rename(
                    temporary.with_name(temporary.name + ".failed-" + str(time.time_ns()))
                )
            prepare(
                Path(plan["configuration"]["input_root"]),
                temporary,
                job["model"],
                include_specs=job["kind"] == "spec",
                source_hashes=[] if job["kind"] == "spec" else [job["sha256"]],
            )
            temporary.rename(bundle)
            result = read_json(bundle / "quality_report.json")
        elif stage == "enrich":
            try:
                validate_bundle(bundle)
            except ValueError:
                if stage_worker(output, job_id, "prepare", concurrency) != 0:
                    raise ValueError("解析检查点恢复失败") from None
            manifest = validate_bundle(bundle)
            if manifest["stage"] == "enriched":
                result = read_json(bundle / "quality_report.json")
            else:
                result = asyncio.run(
                    enrich(bundle, concurrency=concurrency, cache_root=output / "cache")
                )
            if result.get("enrichment_failures"):
                blocked = any(
                    "HTTP " + str(code) in str(f)
                    for f in result["enrichment_failures"]
                    for code in [401, 402, 403]
                )
                write_json(
                    result_path, {"ok": False, "provider_blocked": blocked, "details": result}
                )
                return 2
        elif stage == "index":
            import_bundle(bundle)
            result = asyncio.run(index_bundle(bundle, cache_root=output / "cache"))
            if result["failures"]:
                write_json(result_path, {"ok": False, "details": result})
                return 2
            try:
                report(bundle)
            except Exception as exc:
                result["report_error"] = type(exc).__name__
        else:
            raise ValueError("未知批处理阶段")
        write_json(result_path, {"ok": True, "details": result})
        return 0
    except Exception as exc:
        details = {"ok": False, "error_type": type(exc).__name__}
        if isinstance(exc, ValueError | FileNotFoundError):
            details["message"] = str(exc)[:500]
        write_json(result_path, details)
        progress("batch_stage_failed", job=job_id, stage=stage, **details)
        return 1


def write_status(
    output: Path,
    plan: dict,
    states: dict,
    *,
    running=True,
    blocked=False,
    finalizing=False,
    extra=None,
):
    counts = Counter(s["state"] for s in states.values())
    stages = {
        stage: sum(s["stages"].get(stage) == "done" for s in states.values()) for stage in STAGES
    }
    complete = counts["done"] == len(states)
    state = (
        "finalizing"
        if finalizing
        else "processing"
        if running
        else "provider_blocked"
        if blocked
        else "complete"
        if complete
        else "partial_failed"
    )
    status = {
        "status": state,
        "pid": os.getpid(),
        "updated_at": now(),
        "created_at": plan["created_at"],
        "pipeline_version": PIPELINE_VERSION,
        "target_models": len(plan["configuration"]["models"]),
        "total_jobs": len(states),
        "unique_pdfs": plan["unique_pdfs"],
        "source_pages": plan["source_pages"],
        "counts": dict(counts),
        "stages": stages,
        "active": [
            {"id": k, "stage": s["state"]} for k, s in states.items() if s["state"] in STAGES
        ],
        "failures": [
            {"id": k, "error": s.get("error")} for k, s in states.items() if s["state"] == "failed"
        ],
        **(extra or {}),
    }
    write_json(output / "status.json", status)
    labels = {"prepare": "解析完成", "enrich": "图片处理完成", "index": "入库与索引完成"}
    lines = [
        "# DJI 全量文档处理进度",
        "",
        f"状态：{state}；更新时间：{status['updated_at']}",
        "",
        f"目标 {status['target_models']} 个型号；{plan['unique_pdfs']} 份去重 PDF，"
        f"{plan['source_pages']} 页；任务总数 {len(states)}（含参数页）。",
        "",
    ]
    lines += [f"- {labels[stage]}：{stages[stage]} / {len(states)}" for stage in STAGES]
    lines += [
        f"- 失败任务：{counts['failed']}",
        "",
        "共享文档只处理一次，全部关联型号保留。",
        "图片隔离不代表原图丢弃。尚未全部完成时不能将此进度视为全量验收。",
        "",
        "机器可读状态：[status.json](status.json)；任务清单：[plan.json](plan.json)。",
    ]
    (output / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status


async def run_batch(
    output: Path,
    *,
    parse_workers=2,
    enrich_workers=2,
    concurrency=3,
    attempts=3,
    stage_timeout=86400,
) -> dict:
    from .storage import bootstrap

    plan = read_json(output / "plan.json")
    if plan["configuration"]["pipeline_version"] != PIPELINE_VERSION:
        raise ValueError("批处理版本与当前脚本不一致")
    settings = get_settings()
    if (
        plan["configuration"]["vlm_model"] != settings.vlm_model
        or plan["configuration"]["embedding_model"] != settings.embedding_model
    ):
        raise ValueError("批处理模型配置发生变化，请使用新批次")
    lock = (output / "batch.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise ValueError("该批次已有进程在运行") from exc
    bootstrap()
    states = {j["id"]: read_json(output / "jobs" / (j["id"] + ".json")) for j in plan["jobs"]}
    queues = {stage: asyncio.Queue() for stage in STAGES}
    queues["text_enrich"] = asyncio.Queue()
    blocked = False
    last_snapshot = 0.0
    children = set()

    def enqueue(stage, job):
        lane = stage
        if stage == "enrich":
            try:
                q = read_json(bundle_path(output, job) / "quality_report.json")
                if q.get("element_types", {}).get("figure", 0) == 0:
                    lane = "text_enrich"
            except (OSError, ValueError):
                pass
        queues[lane].put_nowait(job)

    def snapshot(force=False):
        nonlocal last_snapshot
        if force or time.monotonic() - last_snapshot > 10:
            write_status(output, plan, states, blocked=blocked)
            last_snapshot = time.monotonic()

    def save(job_id):
        states[job_id]["updated_at"] = now()
        write_json(output / "jobs" / (job_id + ".json"), states[job_id])
        snapshot()

    for job in plan["jobs"]:
        state = states[job["id"]]
        if state["stages"].get("index") == "done":
            state["state"] = "done"
            continue
        for stage in STAGES:
            if state["stages"].get(stage) != "done":
                state["state"] = "pending"
                enqueue(stage, job)
                break
    snapshot(True)

    async def worker(stage, lane=None):
        nonlocal blocked
        queue = queues[lane or stage]
        while True:
            job = await queue.get()
            if job is None:
                queue.task_done()
                return
            job_id = job["id"]
            state = states[job_id]
            if blocked and stage == "enrich" and lane != "text_enrich":
                state["state"] = "waiting_provider"
                save(job_id)
                queue.task_done()
                continue
            state["state"] = stage
            save(job_id)
            ok = False
            for attempt in range(attempts):
                state["attempts"][stage] = state["attempts"].get(stage, 0) + 1
                save(job_id)
                log_path = output / "logs" / f"{job_id}-{stage}.log"
                log_path.parent.mkdir(exist_ok=True)
                with log_path.open("a") as log:
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "tools.drone_documents",
                        "_batch-stage",
                        "--batch-root",
                        str(output.resolve()),
                        "--job-id",
                        job_id,
                        "--stage",
                        stage,
                        "--concurrency",
                        str(concurrency),
                        cwd=ROOT,
                        stdout=log,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                    children.add(proc)
                    try:
                        timeout = min(
                            stage_timeout,
                            1200
                            if stage == "prepare"
                            else 7200
                            if stage == "index"
                            else stage_timeout,
                        )
                        code = await asyncio.wait_for(proc.wait(), timeout=timeout)
                    except TimeoutError:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=10)
                        except TimeoutError:
                            os.killpg(proc.pid, signal.SIGKILL)
                            await proc.wait()
                        code = -1
                    finally:
                        children.discard(proc)
                result_path = output / "results" / f"{job_id}-{stage}.json"
                result = (
                    read_json(result_path)
                    if result_path.exists()
                    else {"ok": False, "error_type": "TimeoutOrCrash"}
                )
                if code == 0 and result.get("ok"):
                    ok = True
                    state["stages"][stage] = "done"
                    state.pop("error", None)
                    break
                state["error"] = {
                    "stage": stage,
                    "exit_code": code,
                    "error_type": result.get("error_type", "StageFailed"),
                    "details": result.get("message"),
                }
                if result.get("provider_blocked"):
                    blocked = True
                    state["error"]["error_type"] = "ModelProviderUnavailable"
                    break
                if attempt < attempts - 1:
                    await asyncio.sleep(min(30, 5 * (attempt + 1)))
            if ok:
                index = STAGES.index(stage)
                if index + 1 < len(STAGES):
                    state["state"] = "pending"
                    enqueue(STAGES[index + 1], job)
                else:
                    state["state"] = "done"
            else:
                state["state"] = "failed"
            save(job_id)
            queue.task_done()

    async def heartbeat():
        while True:
            snapshot(True)
            await asyncio.sleep(15)

    worker_specs = [
        ("prepare", "prepare", parse_workers),
        ("enrich", "enrich", enrich_workers),
        ("enrich", "text_enrich", 1),
        ("index", "index", 1),
    ]
    tasks = [
        asyncio.create_task(worker(stage, lane))
        for stage, lane, count in worker_specs
        for _ in range(count)
    ]
    pulse = asyncio.create_task(heartbeat())

    async def join_stages():
        for stage in ["prepare", "enrich", "text_enrich", "index"]:
            await queues[stage].join()

    joining = asyncio.create_task(join_stages())
    try:
        done, _ = await asyncio.wait([joining, *tasks], return_when=asyncio.FIRST_COMPLETED)
        if joining not in done:
            for task in done:
                task.result()
            raise RuntimeError("批处理工作进程提前结束")
        await joining
        for _stage, lane, count in worker_specs:
            for _ in range(count):
                queues[lane].put_nowait(None)
        await asyncio.gather(*tasks)
    except Exception as exc:
        status = write_status(output, plan, states, running=False, blocked=blocked)
        status["orchestrator_error"] = type(exc).__name__
        write_json(output / "status.json", status)
        raise
    finally:
        pulse.cancel()
        joining.cancel()
        for proc in children:
            if proc.returncode is None:
                os.killpg(proc.pid, signal.SIGTERM)
        for task in tasks:
            task.cancel()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    status = write_status(
        output,
        plan,
        states,
        running=False,
        blocked=blocked,
        finalizing=all(s["stages"].get("index") == "done" for s in states.values()),
    )
    progress("batch_finished", **status)
    return status
