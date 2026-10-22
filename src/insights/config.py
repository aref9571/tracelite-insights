from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INSIGHTS_", env_file=".env", env_file_encoding="utf-8"
    )

    tracelite_base_url: str = "http://localhost:8080"
    tracelite_management_key: SecretStr | None = None
    tracelite_ingestion_keys: dict[str, SecretStr] = Field(default_factory=dict)
    database_path: Path = Path("data/insights.duckdb")
    api_key: SecretStr | None = None
    request_timeout_seconds: float = Field(default=10.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
