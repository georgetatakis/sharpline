from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.config import get_settings
from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    # A throwaway SQLite file: enough to run every migration without a running Postgres.
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def alembic_config():
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def test_upgrade_creates_all_tables(db_url):
    command.upgrade(alembic_config(), "head")

    tables = set(inspect(create_engine(db_url)).get_table_names())
    assert {"data_snapshots", "team_offense_stats", "team_defense_stats"} <= tables


def test_migrations_match_models(db_url):
    command.upgrade(alembic_config(), "head")

    with create_engine(db_url).connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_downgrade_to_base_removes_tables(db_url):
    config = alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    tables = set(inspect(create_engine(db_url)).get_table_names())
    assert tables <= {"alembic_version"}
