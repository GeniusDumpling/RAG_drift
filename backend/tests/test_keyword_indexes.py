"""Exercise online index recovery in an isolated test-database schema."""
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.no_db


def test_keyword_index_migration_recovers_interrupted_build():
    url = os.environ.get('TEST_DATABASE_URL',
                         'postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag_test')
    assert (make_url(url).database or '').endswith('_test')
    engine = create_engine(url, isolation_level='AUTOCOMMIT')
    schema = 'keyword_indexes_' + uuid.uuid4().hex
    path = Path('backend/alembic/versions/0011_keyword_trigram_indexes.py')
    spec = importlib.util.spec_from_file_location('keyword_indexes_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.connect() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public'))
        conn.execute(text(f'CREATE SCHEMA {schema}'))
        try:
            conn.execute(text(f'SET search_path TO {schema}, public'))
            conn.execute(text(
                'CREATE TABLE content_items(title text, summary_text text, tags jsonb)'
            ))
            conn.execute(text('CREATE TABLE content_chunks(display_text text, embed_text text)'))
            conn.execute(text("INSERT INTO content_chunks VALUES ('same', 'same'),('same','same')"))
            with pytest.raises(IntegrityError):
                conn.execute(text('CREATE UNIQUE INDEX CONCURRENTLY ix_content_chunks_display_trgm '
                                  'ON content_chunks(display_text)'))
            conn.rollback()
            assert conn.execute(text("SELECT indisvalid FROM pg_index WHERE indexrelid = "
                                     "'ix_content_chunks_display_trgm'::regclass"
                                     )).scalar() is False
            conn.commit()
            context = MigrationContext.configure(conn)
            with context.begin_transaction(), Operations.context(context):
                module.upgrade()
            valid = conn.execute(text('SELECT count(*) FROM pg_index i '
                                      'JOIN pg_class c ON c.oid=i.indexrelid '
                                      'JOIN pg_namespace n ON n.oid=c.relnamespace '
                                      'WHERE n.nspname=:schema AND i.indisvalid'),
                                 {'schema': schema})
            assert valid.scalar() == 5
            conn.commit()
            with context.begin_transaction(), Operations.context(context):
                module.downgrade()
            assert conn.execute(text('SELECT count(*) FROM pg_indexes WHERE schemaname=:schema'),
                                {'schema': schema}).scalar() == 0
        finally:
            conn.rollback()
            conn.execute(text('SET search_path TO public'))
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            conn.commit()
    engine.dispose()
