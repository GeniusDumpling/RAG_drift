"""Bounded document build: exact calls, durable case checkpoints, atomic publish, readback."""

import json
from pathlib import Path

from app.services.video_graph.material import LeaseLostError

from .client import VisionClient
from .extraction import extract, policy_id
from .grounding import validate_result
from .queries import retrieve, verify
from .source import ensure_report_path, load_bundle, save_json


async def run_pipeline(
    settings,
    store,
    source_root,
    report_path,
    *,
    cache,
    retry_failed=False,
    cache_only=False,
    dry_run=False,
):
    bundle = load_bundle(source_root)
    ensure_report_path(source_root, report_path)
    policy = policy_id(settings)
    report = dict(
        source_hash=bundle["source_hash"],
        policy=policy,
        manuals={},
        status="running",
        model_profile={
            "llm": getattr(settings, "llm_model", None),
            "vlm": getattr(settings, "document_graph_vlm_model", None)
            or getattr(settings, "vlm_model", None),
        },
    )
    if dry_run:
        return dict(
            status="read_only",
            source_hash=bundle["source_hash"],
            policy=policy,
            manuals=[
                dict(id=m["id"], model=m["model"], pages=len(m["pages"])) for m in bundle["manuals"]
            ],
        )
    if Path(report_path).exists():
        previous = json.loads(Path(report_path).read_text())
        if previous["source_hash"] != bundle["source_hash"] or previous["policy"] != policy:
            raise ValueError("报告属于其他来源或规则，请使用新文件名")
        report["prior_runs"] = previous.get("prior_runs", []) + [
            {"status": previous["status"], "manuals": previous["manuals"]}
        ]
    save_json(report_path, report)
    for manual in bundle["manuals"]:
        sid = manual["id"]
        bid = store.enqueue(manual, bundle, source_root, policy, retry_failed=retry_failed)
        row = dict(model=manual["model"], build_id=bid)
        report["manuals"][sid] = row
        claimed = store.claim(sid, bid)
        if not claimed:
            check = verify(store, sid, bid)
            row.update(
                status="already_current" if check["verified"] else "not_current",
                verification=check,
                trace=[],
                usage={},
            )
            save_json(report_path, report)
            continue
        build, token = claimed
        output = json.loads(build["output_json"])
        output.setdefault("cases", {})
        client = VisionClient(
            settings,
            cache,
            cache_only=cache_only,
            heartbeat=lambda sid=sid, bid=bid, token=token, output=output: store.checkpoint(
                sid, bid, token, output
            ),
        )
        try:
            for case in bundle["cases"]:
                if case["source_id"] != sid:
                    continue
                cid = case["id"]
                if cid in output["cases"]:
                    try:
                        validate_result(
                            bundle, case, output["cases"][cid], policy, root=source_root
                        )
                    except ValueError:
                        del output["cases"][cid]
                    else:
                        continue
                output["cases"][cid] = await extract(bundle, case, client, source_root)
                store.checkpoint(sid, bid, token, output)
                row.update(
                    completed_cases=list(output["cases"]),
                    trace=client.trace,
                    usage=client.usage.snapshot(),
                )
                save_json(report_path, report)
            # Re-read bytes immediately before publication; both source and target remain bound.
            current = load_bundle(source_root)
            if current["source_hash"] != bundle["source_hash"]:
                raise ValueError("文档源已变化")
            store.publish(sid, bid, token, bundle, output)
            check = verify(store, sid, bid)
            probe = retrieve(store, manual["model"], source_id=sid, limit=1)
            has_facts = any(r["facts"] for r in output["cases"].values())
            check["checks"]["retrievable"] = bool(probe["items"]) or not has_facts
            check["verified"] = all(check["checks"].values())
            row.update(
                status="success" if check["verified"] else "verification_failed",
                verification=check,
                retrieval_probe=bool(probe["items"]),
            )
        except LeaseLostError:
            row.update(status="lease_lost")
        except Exception as exc:
            try:
                store.fail(sid, bid, token, output, type(exc).__name__)
            except LeaseLostError:
                pass
            row.update(status="failed", error_type=type(exc).__name__)
        finally:
            row.update(trace=client.trace, usage=client.usage.snapshot())
            save_json(report_path, report)
    report["status"] = (
        "success"
        if all(
            r["status"] in {"success", "already_current"}
            and r.get("verification", {}).get("verified")
            for r in report["manuals"].values()
        )
        else "incomplete"
    )
    save_json(report_path, report)
    return report
