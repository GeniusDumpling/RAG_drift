#!/usr/bin/env python3
"""Back up, move and verify the literature schema before removing its source tables.

Run during a maintenance window with the API and literature worker stopped.
Without --apply this only prints the proposed database names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from psycopg import sql  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402

TABLES = (
    "literature_runs",
    "literature_papers",
    "literature_run_events",
    "literature_run_papers",
    "literature_artifacts",
    "literature_evidence",
)


def connection_url(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def table_fingerprint(connection: psycopg.Connection[Any], name: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    rows = 0
    query = sql.SQL(
        "SELECT id::text, encode(sha256(convert_to(row_to_json(t)::text, 'UTF8')), 'hex') "
        "FROM public.{} AS t ORDER BY id"
    ).format(sql.Identifier(name))
    for row_id, row_hash in connection.execute(query):
        digest.update(f"{row_id}:{row_hash}\n".encode())
        rows += 1
    columns = connection.execute(
        "SELECT column_name, udt_name, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
        "ORDER BY ordinal_position",
        (name,),
    ).fetchall()
    constraints = connection.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid=to_regclass(%s) ORDER BY conname",
        (f"public.{name}",),
    ).fetchall()
    indexes = connection.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename=%s ORDER BY indexname",
        (name,),
    ).fetchall()
    return {
        "rows": rows,
        "sha256": digest.hexdigest(),
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
    }


def prepare_connection(connection: psycopg.Connection[Any]) -> None:
    connection.execute("SET TIME ZONE 'UTC'")
    connection.execute("SET bytea_output = 'hex'")
    connection.execute("SET DateStyle = 'ISO, YMD'")


def migrate(args: argparse.Namespace) -> None:
    settings = get_settings()
    source_url = make_url(settings.sync_database_url)
    target_url = make_url(settings.resolved_literature_sync_database_url)
    endpoint = lambda u: (u.host, u.port, u.username)  # noqa: E731
    if endpoint(source_url) != endpoint(target_url) or source_url.database == target_url.database:
        raise RuntimeError(
            "This migration requires two distinct databases on the same server/user."
        )
    print(
        json.dumps(
            {
                "source": source_url.database,
                "target": target_url.database,
                "tables": TABLES,
                "apply": args.apply,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not args.apply:
        return
    if not args.writers_stopped:
        raise RuntimeError("Stop API/literature writers, then supply --writers-stopped.")

    os.umask(0o077)
    backup_dir = Path(args.backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backup_dir / "literature.dump"
    if backup.exists():
        raise RuntimeError("Backup already exists; use a new backup directory.")
    client = ["docker", "exec", "-i", args.postgres_container]
    user = source_url.username or "postgres"

    with psycopg.connect(connection_url(source_url), autocommit=True) as source:
        prepare_connection(source)
        if source.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (target_url.database,)
        ).fetchone():
            raise RuntimeError("Target database already exists; refusing to overwrite it.")
        actual = {
            r[0]
            for r in source.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename LIKE 'literature_%'"
            )
        }
        if actual != set(TABLES):
            raise RuntimeError(f"Unexpected source literature tables: {sorted(actual)}")

        with source.transaction():
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            source.execute("SET LOCAL lock_timeout = '10s'")
            names = sql.SQL(", ").join(sql.Identifier("public", name) for name in TABLES)
            source.execute(sql.SQL("LOCK TABLE {} IN SHARE MODE").format(names))
            active = source.execute(
                "SELECT count(*) FROM literature_runs "
                "WHERE status NOT IN ('success','partial','failed','cancelled')"
            ).fetchone()[0]
            if active:
                raise RuntimeError(
                    "Active literature tasks remain; do not move them mid-execution."
                )
            snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
            source_state = {name: table_fingerprint(source, name) for name in TABLES}
            dump_command = client + [
                "pg_dump",
                "-U",
                user,
                "-d",
                source_url.database,
                "-Fc",
                "--no-owner",
                "--no-acl",
                "--snapshot",
                snapshot,
            ]
            for name in TABLES:
                dump_command.extend(["--table", f"public.{name}"])
            with backup.open("xb") as output:
                subprocess.run(dump_command, stdout=output, check=True)
                output.flush()
                os.fsync(output.fileno())
            with backup.open("rb") as archive:
                backup_hash = hashlib.file_digest(archive, "sha256").hexdigest()
            print(f"Backup completed: {backup} ({backup.stat().st_size} bytes)", flush=True)

            # CREATE DATABASE must run outside the source transaction.
            with psycopg.connect(connection_url(source_url), autocommit=True) as admin:
                admin.execute(
                    sql.SQL("CREATE DATABASE {} WITH TEMPLATE template0 OWNER {}").format(
                        sql.Identifier(target_url.database), sql.Identifier(user)
                    )
                )
            with backup.open("rb") as archive:
                subprocess.run(
                    client
                    + [
                        "pg_restore",
                        "-U",
                        user,
                        "-d",
                        target_url.database,
                        "--no-owner",
                        "--no-acl",
                        "--exit-on-error",
                        "--single-transaction",
                    ],
                    stdin=archive,
                    check=True,
                )
            with psycopg.connect(connection_url(target_url), autocommit=True) as target:
                prepare_connection(target)
                target_state = {name: table_fingerprint(target, name) for name in TABLES}
                if source_state != target_state:
                    raise RuntimeError(
                        "Target schema/data checksum differs; source tables preserved."
                    )
            # Restored tables already match the frozen initial literature revision.
            command.stamp(Config(str(ROOT / "alembic.ini"), ini_section="literature"), "head")
            manifest = {
                "checked_at": datetime.now(UTC).isoformat(),
                "source": source_url.database,
                "target": target_url.database,
                "backup": str(backup),
                "backup_sha256": backup_hash,
                "tables": source_state,
                "verified": True,
                "source_tables_removed": False,
            }
            manifest_path = backup_dir / "verification.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            print("All row hashes, column definitions, constraints and indexes match.", flush=True)
            # RESTRICT is intentional: never drop an unrelated dependent object.
            source.execute(sql.SQL("DROP TABLE {} RESTRICT").format(names))
        manifest["source_tables_removed"] = True
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("Migration committed; source literature tables removed.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--postgres-container", default="intelligence-rag-postgres-deploy")
    parser.add_argument(
        "--backup-dir",
        default=str(
            ROOT / "data" / "backups" / datetime.now(UTC).strftime("literature-%Y%m%dT%H%M%SZ")
        ),
    )
    migrate(parser.parse_args())


if __name__ == "__main__":
    main()
