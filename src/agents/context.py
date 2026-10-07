"""Infrastructure-neutral dependencies used by Agent roles."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class ModelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ModelCallConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int = Field(default=42, ge=0)
    think: bool | None = None
    timeout_s: float = Field(default=30.0, gt=0.0, le=120.0)
    max_retries: int = Field(default=2, ge=0, le=2)
    prompt_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


ModelT = TypeVar("ModelT", bound=BaseModel)


class ModelClient(Protocol):
    async def complete_structured(
        self,
        *,
        messages: Sequence[ModelMessage],
        output_model: type[ModelT],
        config: ModelCallConfig,
    ) -> ModelT: ...
