"""Configuration loading tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_settings_load_defaults() -> None:
    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        APP_NAME="edge-platform",
        DATABASE_URL="postgresql+psycopg://edge:edge@localhost:5432/edge_platform",
    )

    assert settings.app_name == "edge-platform"
    assert settings.cors_origins == ["http://localhost:5173"]
    assert "postgresql" in settings.database_url


def test_cors_origins_split() -> None:
    settings = Settings(
        _env_file=None,
        BACKEND_CORS_ORIGINS="http://a.example, http://b.example",
    )

    assert settings.cors_origins == ["http://a.example", "http://b.example"]


def test_log_format_defaults_to_console() -> None:
    settings = Settings(_env_file=None)

    assert settings.log_format == "console"


def test_log_format_reads_json_from_env() -> None:
    settings = Settings(_env_file=None, LOG_FORMAT="json")

    assert settings.log_format == "json"


def test_log_format_treats_empty_and_false_as_console() -> None:
    assert Settings(_env_file=None, LOG_FORMAT="").log_format == "console"
    assert Settings(_env_file=None, LOG_FORMAT="false").log_format == "console"


def test_log_format_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LOG_FORMAT="xml")


def test_observability_backend_defaults_to_prometheus() -> None:
    settings = Settings(_env_file=None)

    assert settings.observability_backend == "prometheus"


def test_observability_backend_accepts_cloudwatch() -> None:
    settings = Settings(_env_file=None, OBSERVABILITY_BACKEND="cloudwatch")

    assert settings.observability_backend == "cloudwatch"


def test_observability_backend_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, OBSERVABILITY_BACKEND="datadog")
