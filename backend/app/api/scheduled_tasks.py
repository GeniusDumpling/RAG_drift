"""Narrow proxy to the host scheduler; never expose arbitrary systemctl execution."""

import hmac
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/scheduled-tasks", tags=["scheduled-tasks"])
TASK_IDS = {"video-collect", "discourse-collect", "supplier-pipeline", "supplier-verify"}


def bridge_request(method: str, path: str) -> dict:
    socket = os.environ.get("SCHEDULER_SOCKET", "")
    if not socket:
        raise HTTPException(503, "尚未配置宿主机定时任务接口")
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=socket), timeout=50) as client:
            response = client.request(method, "http://scheduler" + path)
        if response.status_code >= 400:
            raise HTTPException(response.status_code, response.json().get("detail", "调度失败"))
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "宿主机定时任务接口不可用") from exc


@router.get("")
def list_scheduled_tasks() -> dict:
    return bridge_request("GET", "/tasks")


@router.post("/{task_id}/trigger", status_code=202)
def trigger_scheduled_task(task_id: str, request: Request) -> dict:
    if task_id not in TASK_IDS:
        raise HTTPException(404, "未知采集任务")
    # Operator secret stays in memory in the UI; never bake it into frontend assets.
    try:
        token = (
            Path(os.environ.get("SCHEDULER_TOKEN_FILE", "/run/rag-scheduler/token"))
            .read_text()
            .strip()
        )
    except OSError as exc:
        raise HTTPException(503, "尚未配置采集操作口令") from exc
    authorization = request.headers.get("authorization", "")
    if not token or not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
        raise HTTPException(403, "采集操作口令不正确")
    return bridge_request("POST", "/tasks/" + task_id)
