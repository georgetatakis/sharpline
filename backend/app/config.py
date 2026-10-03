from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# One .env at the repo root is shared by the backend and Docker Compose.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """App configuration, read from environment variables, then the root .env file.

    Real environment variables take precedence over .env, so hosted deploys can
    set values through the host's secret manager without any file present.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds POSTGRES_* vars meant for Docker Compose
    )

    database_url: str
    # SecretStr keeps the key out of logs and reprs; read it with .get_secret_value().
    odds_api_key: SecretStr = SecretStr("")
    log_level: str = "INFO"
    # Browser origin allowed to call the API (the React dev server by default).
    frontend_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
