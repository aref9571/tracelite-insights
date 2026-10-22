from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsError

from insights.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_are_used_when_nothing_is_configured():
    settings = Settings()

    assert settings.tracelite_base_url == "http://localhost:8080"
    assert settings.tracelite_management_key is None
    assert settings.tracelite_ingestion_keys == {}
    assert settings.database_path == Path("data/insights.duckdb")
    assert settings.api_key is None
    assert settings.request_timeout_seconds == 10.0


def test_values_are_read_from_prefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("INSIGHTS_TRACELITE_BASE_URL", "http://tracelite:9000")
    monkeypatch.setenv("INSIGHTS_TRACELITE_MANAGEMENT_KEY", "management-secret")
    monkeypatch.setenv("INSIGHTS_DATABASE_PATH", "/var/lib/insights/store.duckdb")
    monkeypatch.setenv("INSIGHTS_API_KEY", "insights-secret")
    monkeypatch.setenv("INSIGHTS_REQUEST_TIMEOUT_SECONDS", "2.5")

    settings = Settings()

    assert settings.tracelite_base_url == "http://tracelite:9000"
    assert settings.tracelite_management_key.get_secret_value() == "management-secret"
    assert settings.database_path == Path("/var/lib/insights/store.duckdb")
    assert settings.api_key.get_secret_value() == "insights-secret"
    assert settings.request_timeout_seconds == 2.5


def test_ingestion_keys_are_parsed_from_json(monkeypatch):
    monkeypatch.setenv(
        "INSIGHTS_TRACELITE_INGESTION_KEYS",
        '{"checkout-service": "checkout-secret", "auth-service": "auth-secret"}',
    )

    settings = Settings()

    keys = {app: key.get_secret_value() for app, key in settings.tracelite_ingestion_keys.items()}
    assert keys == {"checkout-service": "checkout-secret", "auth-service": "auth-secret"}


def test_unprefixed_environment_variables_are_ignored(monkeypatch):
    monkeypatch.setenv("API_KEY", "unrelated-secret")

    assert Settings().api_key is None


def test_values_are_read_from_dotenv_file_in_working_directory(tmp_path):
    (tmp_path / ".env").write_text("INSIGHTS_API_KEY=from-dotenv\n", encoding="utf-8")

    assert Settings().api_key.get_secret_value() == "from-dotenv"


def test_invalid_ingestion_keys_json_is_rejected(monkeypatch):
    monkeypatch.setenv("INSIGHTS_TRACELITE_INGESTION_KEYS", "not-json")

    with pytest.raises(SettingsError, match="tracelite_ingestion_keys"):
        Settings()


@pytest.mark.parametrize("timeout", ["0", "-1", "soon"])
def test_request_timeout_must_be_a_positive_number(monkeypatch, timeout):
    monkeypatch.setenv("INSIGHTS_REQUEST_TIMEOUT_SECONDS", timeout)

    with pytest.raises(ValidationError, match="request_timeout_seconds"):
        Settings()


def test_secrets_never_appear_in_repr_or_str(monkeypatch):
    monkeypatch.setenv("INSIGHTS_TRACELITE_MANAGEMENT_KEY", "management-secret")
    monkeypatch.setenv("INSIGHTS_TRACELITE_INGESTION_KEYS", '{"auth-service": "auth-secret"}')
    monkeypatch.setenv("INSIGHTS_API_KEY", "insights-secret")

    settings = Settings()

    for text in (repr(settings), str(settings)):
        assert "management-secret" not in text
        assert "auth-secret" not in text
        assert "insights-secret" not in text
        assert "**********" in text


def test_get_settings_returns_the_same_cached_instance():
    assert get_settings() is get_settings()


def test_get_settings_reads_environment_after_cache_clear(monkeypatch):
    monkeypatch.setenv("INSIGHTS_DATABASE_PATH", "first.duckdb")
    first = get_settings()
    monkeypatch.setenv("INSIGHTS_DATABASE_PATH", "second.duckdb")

    assert get_settings().database_path == Path("first.duckdb")
    get_settings.cache_clear()
    assert get_settings().database_path == Path("second.duckdb")
    assert first.database_path == Path("first.duckdb")
