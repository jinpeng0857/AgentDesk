"""Configuration parsing and import-safety contracts."""

from __future__ import annotations

import importlib
import sys

import asyncpg
import pytest

from backend.config import AppSettings, ConfigurationError

VALID_SECRET = "a-valid-test-secret-with-at-least-32-bytes"


def test_settings_normalize_checkpoint_url() -> None:
    settings = AppSettings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/agentdesk_test",
        redis_url="redis://localhost:6379/15",
        jwt_secret=VALID_SECRET,
    )
    assert settings.checkpoint_database_url == "postgresql://u:p@localhost:5432/agentdesk_test"


def test_importing_db_session_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing backend.db.session must not open a connection")

    monkeypatch.setattr(asyncpg, "connect", _fail_if_called)
    sys.modules.pop("backend.db.session", None)
    importlib.import_module("backend.db.session")


def test_readiness_rejects_short_secret() -> None:
    settings = AppSettings(jwt_secret="too-short")
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        settings.validate_readiness(require_model=False)


def test_readiness_rejects_placeholder_secret() -> None:
    settings = AppSettings(jwt_secret="replace-with-at-least-32-random-bytes")
    with pytest.raises(ConfigurationError, match="placeholder"):
        settings.validate_readiness(require_model=False)


def test_readiness_requires_model_for_worker() -> None:
    settings = AppSettings(jwt_secret=VALID_SECRET, openai_model="")
    with pytest.raises(ConfigurationError, match="OPENAI_MODEL"):
        settings.validate_readiness(require_model=True)


def test_readiness_accepts_complete_configuration() -> None:
    settings = AppSettings(jwt_secret=VALID_SECRET, openai_model="gpt-4o-mini")
    settings.validate_readiness(require_model=True)
