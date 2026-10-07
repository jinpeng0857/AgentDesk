"""Side-effect free configuration parsing.

Importing this module must never contact PostgreSQL, Redis or a model provider.
Readiness validation is explicit so the API process can import the settings for
OpenAPI generation while the Worker refuses to start with placeholder secrets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_BYTES = 32
PLACEHOLDER_SECRET_MARKERS = ("replace-with", "changeme", "change-me", "placeholder", "todo")


class ConfigurationError(RuntimeError):
    """Raised when configuration cannot safely serve a runtime process."""


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    database_url: str = "postgresql+asyncpg://agentdesk:agentdesk@localhost:5432/agentdesk"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "agentdesk"
    jwt_audience: str = "agentdesk-api"
    jwt_ttl_s: int = 900
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = ""
    openai_think: bool | None = None
    openai_timeout_s: float = 30.0
    openai_max_retries: int = 2
    chroma_path: Path = Path("data/chroma")
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def checkpoint_database_url(self) -> str:
        """Psycopg connection string derived without a second database secret."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def validate_readiness(self, *, require_model: bool) -> None:
        secret = self.jwt_secret.get_secret_value()
        if len(secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            raise ConfigurationError(
                f"JWT_SECRET must be at least {MIN_JWT_SECRET_BYTES} bytes"
            )
        lowered = secret.lower()
        if any(marker in lowered for marker in PLACEHOLDER_SECRET_MARKERS):
            raise ConfigurationError("JWT_SECRET must not be a placeholder value")
        if require_model and not self.openai_model.strip():
            raise ConfigurationError("OPENAI_MODEL must be set before the worker starts")
