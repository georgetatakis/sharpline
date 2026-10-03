import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for var in ("DATABASE_URL", "ODDS_API_KEY", "LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)


def test_reads_values_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/test")
    monkeypatch.setenv("ODDS_API_KEY", "abc123")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/test"
    assert settings.odds_api_key.get_secret_value() == "abc123"
    assert settings.log_level == "DEBUG"


def test_reads_values_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql+psycopg://u:p@localhost/fromfile\n")

    settings = Settings(_env_file=env_file)

    assert settings.database_url == "postgresql+psycopg://u:p@localhost/fromfile"


def test_environment_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql+psycopg://u:p@localhost/fromfile\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/fromenv")

    settings = Settings(_env_file=env_file)

    assert settings.database_url == "postgresql+psycopg://u:p@localhost/fromenv"


def test_missing_database_url_fails_fast():
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_optional_values_have_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")

    settings = Settings(_env_file=None)

    assert settings.odds_api_key.get_secret_value() == ""
    assert settings.log_level == "INFO"


def test_odds_api_key_is_masked_in_repr(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("ODDS_API_KEY", "super-secret-key")

    settings = Settings(_env_file=None)

    assert "super-secret-key" not in repr(settings)
