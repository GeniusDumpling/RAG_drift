from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from tools.ifixit_rag.bundle import load_bundle
from tools.ifixit_rag.chunking import Budget
from tools.ifixit_rag.prepare import prepare


class WordTokenizer:
    def encode(self, text, **kwargs):
        return [0, *text.split(), 1]


def sample_guide(guide_id=1, steps=None, **extra):
    return {
        "guideid": guide_id,
        "title": "DJI Mini 2 Battery Replacement",
        "category": "DJI Mini 2",
        "subject": "Battery",
        "locale": "en",
        "type": "replacement",
        "difficulty": "Easy",
        "time_required": "5 minutes",
        "time_required_min": 300,
        "time_required_max": 300,
        "introduction_raw": "Replace the battery using this guide.",
        "conclusion_raw": "Follow the steps in reverse order to reassemble.",
        "steps": steps
        if steps is not None
        else [
            {
                "stepid": 11,
                "guideid": guide_id,
                "orderby": 1,
                "revisionid": 42,
                "title": "Battery",
                "lines": [
                    {"text_raw": "Remove the 2.5 mm screw.", "bullet": "red", "level": 0},
                    {
                        "text_raw": "Do not puncture the battery.",
                        "bullet": "icon_caution",
                        "level": 1,
                    },
                ],
                "media": {
                    "type": "image",
                    "data": [
                        {"id": 10, "guid": "imageA", "original": "https://example.com/a.full"}
                    ],
                },
            }
        ],
        "tools": [{"text": "Phillips #0", "quantity": 1}],
        "parts": [],
        "prerequisites": [],
        "revisionid": 3,
        "published_date": 1494382748,
        "modified_date": 1582913430,
        "url": f"https://www.ifixit.com/Guide/{guide_id}",
        "author": {"userid": 99, "username": "Example Author", "unique_username": "example"},
        **extra,
    }


def write_guide(directory: Path, payload: dict) -> Path:
    path = directory / f"{payload['guideid']}_guide" / "原始JSON.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Guide\n\n```json\n" + json.dumps(payload) + "\n```\n", encoding="utf-8")
    return path


@pytest.fixture
def budget():
    return Budget(max_tokens=480, max_chars=4000, tokenizer=WordTokenizer(), tokenizer_name="test")


@pytest.fixture
def make_bundle(tmp_path, budget):
    counter = 0

    def make(*payloads):
        nonlocal counter
        counter += 1
        source = tmp_path / f"input-{counter}"
        output = tmp_path / f"bundle-{counter}"
        for payload in payloads or (sample_guide(),):
            write_guide(source, payload)
        prepare(source, output, budget)
        return load_bundle(output)

    return make


@pytest.fixture
def db_session():
    url = os.environ.get("IFIXIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set IFIXIT_TEST_DATABASE_URL for isolated PostgreSQL integration tests")
    from app.db.base import Base
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from tools.ifixit_rag.database import Session

    database = make_url(url).database or ""
    if not database.endswith("_test"):
        raise RuntimeError("IFIXIT_TEST_DATABASE_URL database name must end in _test")
    engine = create_engine(url)
    schema = "ifixit_test_" + uuid.uuid4().hex
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.commit()
        connection.execute(text(f'SET search_path TO "{schema}"'))
        connection.commit()
        Base.metadata.create_all(connection)
        connection.commit()
        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                yield session
        finally:
            connection.rollback()
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.commit()
    engine.dispose()
