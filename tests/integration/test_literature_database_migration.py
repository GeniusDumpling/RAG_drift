"""Opt-in, real PostgreSQL round trip using two exclusively owned disposable databases."""

import argparse
import importlib.util
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from app.api.deps import get_session
from app.core.config import get_settings
from app.db.literature import get_literature_session
from app.main import create_app
from app.models.literature import (
    LiteratureArtifact,
    LiteratureEvidence,
    LiteraturePaper,
    LiteratureRun,
    LiteratureRunEvent,
    LiteratureRunPaper,
)
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.no_db,
    pytest.mark.skipif(
        os.environ.get("RUN_LITERATURE_MIGRATION_TESTS") != "1",
        reason="Opt in to creating disposable PostgreSQL databases",
    ),
]
ROOT = Path(__file__).resolve().parents[2]


def test_fresh_install_creates_two_independent_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_url = make_url(get_settings().sync_database_url)
    dsn = admin_url.set(drivername="postgresql").render_as_string(False)
    suffix = uuid.uuid4().hex[:12]
    main_name, literature_name = f"lit_main_{suffix}_test", f"lit_fresh_{suffix}_test"
    try:
        with psycopg.connect(dsn, autocommit=True) as admin:
            for name in (main_name, literature_name):
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name))
                )
        for key, name in (
            ("DATABASE_URL", main_name),
            ("SYNC_DATABASE_URL", main_name),
            ("LITERATURE_DATABASE_URL", literature_name),
            ("LITERATURE_SYNC_DATABASE_URL", literature_name),
        ):
            monkeypatch.setenv(key, admin_url.set(database=name).render_as_string(False))
        get_settings.cache_clear()
        for section in ("alembic", "literature"):
            config = Config(str(ROOT / "alembic.ini"), ini_section=section)
            command.upgrade(config, "head")
            command.upgrade(config, "head")
        for name, has_literature in ((main_name, False), (literature_name, True)):
            with psycopg.connect(
                admin_url.set(drivername="postgresql", database=name).render_as_string(False)
            ) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                }
                assert any(t.startswith("literature_") for t in tables) is has_literature
                if has_literature:
                    assert len(tables) == 7  # Six domain tables and their own Alembic version.
                else:
                    assert {"content_items", "source_sites", "chat_sessions"} <= tables
    finally:
        get_settings.cache_clear()
        with psycopg.connect(dsn, autocommit=True) as admin:
            for name in (literature_name, main_name):
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )


def test_migration_preserves_payloads_blocks_unsafe_upgrade_and_routes_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    admin_url = make_url(get_settings().sync_database_url)
    suffix = uuid.uuid4().hex[:12]
    source_name = f"lit_source_{suffix}_test"
    target_name = f"lit_target_{suffix}_test"
    source_url = admin_url.set(database=source_name)
    target_url = admin_url.set(database=target_name)
    spec = importlib.util.spec_from_file_location(
        "literature_migration", ROOT / "scripts/migrate_literature_database.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        with psycopg.connect(module.connection_url(admin_url), autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(source_name))
            )
        monkeypatch.setenv("LITERATURE_DATABASE_URL", source_url.render_as_string(False))
        monkeypatch.setenv("LITERATURE_SYNC_DATABASE_URL", source_url.render_as_string(False))
        get_settings.cache_clear()
        command.upgrade(Config(str(ROOT / "alembic.ini"), ini_section="literature"), "head")
        engine = create_engine(source_url)
        payload = b"%PDF-1.7\x00binary-payload\xff"
        with Session(engine) as session, session.begin():
            run = LiteratureRun(query="迁移回归：激光雷达", status="success")
            paper = LiteraturePaper(title="Lidar", title_hash="title-hash", metadata_hash="meta")
            session.add_all([run, paper])
            session.flush()
            link = LiteratureRunPaper(
                literature_run_id=run.id, paper_id=paper.id, direction_id="D1", search_query="lidar"
            )
            session.add(link)
            session.flush()
            artifact = LiteratureArtifact(
                literature_run_id=run.id,
                paper_id=paper.id,
                artifact_type="paper_pdf",
                binary_data=payload,
                mime_type="application/pdf",
                byte_size=len(payload),
            )
            session.add_all(
                [
                    artifact,
                    LiteratureRunEvent(
                        literature_run_id=run.id,
                        stage="done",
                        event_type="finished",
                        message="完整保留中文",
                    ),
                    LiteratureEvidence(
                        literature_run_paper_id=link.id,
                        matched_term="激光雷达",
                        match_level="exact",
                        evidence_text="Unicode ✓",
                        verified=True,
                    ),
                ]
            )
            session.flush()
            run_id, artifact_id = run.id, artifact.id
        engine.dispose()
        with psycopg.connect(module.connection_url(source_url), autocommit=True) as source:
            source.execute("UPDATE alembic_version SET version_num='0007_chat_sessions'")
        monkeypatch.setenv("DATABASE_URL", source_url.render_as_string(False))
        monkeypatch.setenv("SYNC_DATABASE_URL", source_url.render_as_string(False))
        monkeypatch.setenv("LITERATURE_DATABASE_URL", target_url.render_as_string(False))
        monkeypatch.setenv("LITERATURE_SYNC_DATABASE_URL", target_url.render_as_string(False))
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match="Literature data still exists"):
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        module.migrate(
            argparse.Namespace(
                apply=True,
                writers_stopped=True,
                backup_dir=str(tmp_path / "backup"),
                postgres_container="intelligence-rag-postgres-deploy",
            )
        )
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        with psycopg.connect(module.connection_url(source_url)) as source:
            assert (
                source.execute(
                    "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                    "AND tablename LIKE 'literature_%'"
                ).fetchone()[0]
                == 0
            )
            assert source.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
                "0008_literature_database_split"
            )
        target_engine = create_engine(target_url)
        with Session(target_engine) as session:
            assert session.scalar(select(LiteratureArtifact)).binary_data == payload
            assert session.scalar(select(LiteratureEvidence)).evidence_text == "Unicode ✓"
        target_engine.dispose()

        app = create_app(frontend_dist_dir=tmp_path)

        async def literature_session():
            # An engine per request avoids sharing async connections across TestClient loops.
            async_engine = create_async_engine(target_url)
            try:
                async with async_sessionmaker(async_engine)() as session:
                    yield session
            finally:
                await async_engine.dispose()

        async def forbidden_main_session():
            raise AssertionError("Literature API attempted to access the main database")
            yield

        app.dependency_overrides[get_literature_session] = literature_session
        app.dependency_overrides[get_session] = forbidden_main_session
        with TestClient(app) as client:
            response = client.get("/literature-runs")
            assert response.status_code == 200
            assert response.json()["items"][0]["query"] == "迁移回归：激光雷达"
            result = client.get(f"/literature-runs/{run_id}/results")
            assert result.status_code == 200
            assert result.json()["items"][0]["evidence"][0]["verified"] is True
            download = client.get(f"/literature-artifacts/{artifact_id}/download")
            assert download.status_code == 200
            assert download.content == payload
    finally:
        get_settings.cache_clear()
        with psycopg.connect(module.connection_url(admin_url), autocommit=True) as admin:
            for name in (target_name, source_name):
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
