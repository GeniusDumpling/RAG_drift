#!/usr/bin/env python3
"""Render/install two user systemd jobs; enabling them is an explicit systemctl step.

Use --unit-dir /tmp/forum-units to stage files before systemd-analyze --user verify.
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INCREMENTAL_SCHEDULE = "*-*-* 00/2:30:00 Asia/Shanghai"
BACKFILL_SCHEDULE = "*-*-* *:05:00 Asia/Shanghai"


def quote_value(value: str, *, exec_arg=False) -> str:
    if any(c in value for c in ("\n", "\r", "\0")):
        raise ValueError("Unit paths must not contain control characters")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if exec_arg:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def render_units(root: Path) -> dict[str, str]:
    units = {}
    for name, description, script, calendar, timeout in (
        (
            "rag-discourse-collect",
            "RAG forum incremental capture and processing",
            "run_scheduled_discourse.sh",
            INCREMENTAL_SCHEDULE,
            "30min",
        ),
        (
            "rag-discourse-backfill",
            "RAG forum historical backfill and processing",
            "run_scheduled_backfill.sh",
            BACKFILL_SCHEDULE,
            "20min",
        ),
    ):
        entrypoint = root / "tools/forum-crawler/scripts" / script
        if not entrypoint.is_file():
            raise ValueError(f"Missing forum entrypoint: {entrypoint}")
        units[name + ".service"] = (
            '[Unit]\n'
            f'Description={description}\n'
            '\n'
            '[Service]\nType=oneshot\n'
            f'WorkingDirectory={str(root).replace("%", "%%")}\n'
            f'EnvironmentFile=-{str(root / ".env").replace("%", "%%")}\n'
            f'ExecStart=/bin/bash {quote_value(str(entrypoint), exec_arg=True)}\n'
            f'TimeoutStartSec={timeout}\n'
            'TimeoutStopSec=30s\nKillMode=control-group\n'
            'StandardOutput=journal\nStandardError=journal\n'
        )
        units[name + ".timer"] = (
            "[Unit]\n"
            f"Description=Schedule {description}\n\n"
            "[Timer]\n"
            f"OnCalendar={calendar}\n"
            "Persistent=true\nAccuracySec=1min\n"
            f"Unit={name}.service\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
    return units


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--unit-dir", type=Path, default=Path.home() / ".config/systemd/user")
    args = parser.parse_args(argv)
    units = render_units(args.project_root.resolve())
    args.unit_dir.mkdir(parents=True, exist_ok=True)
    for name, contents in units.items():
        target = args.unit_dir / name
        # Replace complete files atomically; do not follow an existing symlink.
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(contents)
        temporary.chmod(0o644)
        temporary.replace(target)
        print(target)


if __name__ == "__main__":
    main()
