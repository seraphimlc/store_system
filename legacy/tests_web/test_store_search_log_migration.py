"""The new ops-only audit table migrates without rebuilding business tables."""
import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from app.models import BdStoreSearchLog


def test_search_audit_migration_roundtrip(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'migrations/versions/d8e9f0a1b2c3_bd_store_search_log.py'
    spec = importlib.util.spec_from_file_location('search_audit_migration', source)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f'sqlite:///{tmp_path / "audit.db"}')
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert set(inspect(connection).get_table_names()) == {'bd_store_search_log'}
            assert {c['name'] for c in inspect(connection).get_columns('bd_store_search_log')} == set(BdStoreSearchLog.__table__.columns.keys())
            assert len(inspect(connection).get_indexes('bd_store_search_log')) == 2
            migration.downgrade()
            assert not inspect(connection).has_table('bd_store_search_log')
    engine.dispose()
