"""Host-only systemd bridge. Unix socket, four fixed tasks, no shell commands."""

import argparse
import json
import os
import socketserver
import subprocess
from http.server import BaseHTTPRequestHandler
from pathlib import Path

TASKS = {
    "video-collect": "视频采集",
    "discourse-collect": "论坛采集",
    "supplier-pipeline": "供应商抽取",
    "supplier-verify": "供应商验证",
}
SCOPE = "user"
PREFIX = "rag-"
FIELDS = (
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "TimersCalendar",
    "NextElapseUSecRealtime",
    "LastTriggerUSec",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
    "Result",
    "ExecMainStatus",
    "InvocationID",
)


def systemctl(*args):
    command = ["systemctl"] + (["--user"] if SCOPE == "user" else [])
    return subprocess.run(
        command + list(args), check=True, capture_output=True, text=True, timeout=10
    ).stdout


def properties(unit):
    return dict(
        line.split("=", 1)
        for line in systemctl("show", unit, "--property=" + ",".join(FIELDS)).splitlines()
        if "=" in line
    )


def list_tasks():
    items = []
    for task_id, name in TASKS.items():
        unit = PREFIX + task_id
        timer = properties(unit + ".timer")
        if timer.get("LoadState") != "loaded":
            continue
        service = properties(unit + ".service")
        items.append(
            {
                "id": task_id,
                "name": name,
                "timer": unit + ".timer",
                "scope": SCOPE,
                "schedule": timer.get("TimersCalendar", ""),
                "timer_state": timer.get("ActiveState", "unknown"),
                "enabled": timer.get("UnitFileState", ""),
                "next_run": timer.get("NextElapseUSecRealtime", ""),
                "last_run": service.get("ExecMainStartTimestamp", ""),
                "service_state": service.get("ActiveState", "unknown"),
                "last_result": service.get("Result", ""),
                "exit_code": service.get("ExecMainStatus", ""),
                "available": service.get("LoadState") == "loaded",
            }
        )
    return {"items": items}


def trigger(task_id):
    if task_id not in TASKS:
        raise ValueError("未知采集任务")
    unit = PREFIX + task_id + ".service"
    state = properties(unit)
    if state.get("LoadState") != "loaded":
        raise ValueError("任务未安装")
    if state.get("ActiveState") in ("active", "activating", "deactivating"):
        return {"status": "already_running", "invocation_id": state.get("InvocationID", "")}
    # systemd coalesces concurrent starts of the same service; never restart running work.
    systemctl("start", "--no-block", unit)
    state = properties(unit)
    return {
        "status": "accepted",
        "invocation_id": state.get("InvocationID", ""),
        "service_state": state.get("ActiveState", "unknown"),
    }


class Handler(BaseHTTPRequestHandler):
    def handle_request(self):
        try:
            if self.command == "GET" and self.path == "/tasks":
                body, status = list_tasks(), 200
            elif self.command == "POST" and self.path.startswith("/tasks/"):
                task_id = self.path.removeprefix("/tasks/")
                body, status = trigger(task_id), 202
            else:
                body, status = {"detail": "Not found"}, 404
        except ValueError as exc:
            body, status = {"detail": str(exc)}, 404
        except (subprocess.SubprocessError, OSError):
            body, status = {"detail": "无法访问 systemd，请检查宿主机桥接服务权限"}, 503
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = handle_request
    do_POST = handle_request

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--scope", choices=["user", "system"], default="user")
    args = parser.parse_args()
    SCOPE = args.scope
    PREFIX = "rag-" if SCOPE == "user" else "intelligence-rag-"
    path = Path(args.socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    if path.exists():
        if not path.is_socket():
            raise SystemExit("Refusing to replace non-socket path")
        path.unlink()
    with socketserver.UnixStreamServer(str(path), Handler) as server:
        os.chmod(path, 0o600)
        server.serve_forever()
