"""Deterministic model client used by unit tests and offline CI.

The fake returns queued responses in a fixed order, raises queued exceptions
verbatim and records every call, so no test depends on provider behaviour,
network access or randomness.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agents.context import ModelCallConfig, ModelMessage
from backend.integrations.model_client import ModelOutputInvalid, summarize_validation_error

ModelT = TypeVar("ModelT", bound=BaseModel)

FakeResponse = BaseModel | Mapping[str, object] | Exception


@dataclass(frozen=True)
class RecordedCall:
    messages: list[ModelMessage]
    output_model: type[BaseModel]
    config: ModelCallConfig


@dataclass
class FakeModelClient:
    """Implements :class:`agents.context.ModelClient` without any provider."""

    responses: Sequence[FakeResponse]
    calls: list[RecordedCall] = field(default_factory=list)
    _index: int = 0

    async def complete_structured(
        self,
        *,
        messages: Sequence[ModelMessage],
        output_model: type[ModelT],
        config: ModelCallConfig,
    ) -> ModelT:
        self.calls.append(
            RecordedCall(
                messages=list(messages),
                output_model=output_model,
                config=config,
            )
        )

        if self._index >= len(self.responses):
            raise AssertionError(
                f"FakeModelClient exhausted: call {self._index + 1} has no queued response"
            )

        response = self.responses[self._index]
        self._index += 1

        if isinstance(response, Exception):
            raise response
        if isinstance(response, Mapping):
            try:
                return output_model.model_validate(dict(response))
            except ValidationError as first_error:
                errors = summarize_validation_error(first_error)
                for _attempt in range(config.max_retries):
                    if self._index >= len(self.responses):
                        break
                    repair_resp = self.responses[self._index]
                    self._index += 1
                    self.calls.append(
                        RecordedCall(
                            messages=list(messages),
                            output_model=output_model,
                            config=config,
                        )
                    )
                    if isinstance(repair_resp, Exception):
                        raise repair_resp from first_error
                    if isinstance(repair_resp, output_model):
                        return repair_resp
                    if isinstance(repair_resp, Mapping):
                        try:
                            return output_model.model_validate(dict(repair_resp))
                        except ValidationError as repair_err:
                            errors = summarize_validation_error(repair_err)
                            continue
                msg = (
                    f"model did not return valid {output_model.__name__} "
                    f"after repair attempts: {errors}"
                )
                raise ModelOutputInvalid(msg) from first_error
        if not isinstance(response, output_model):
            raise TypeError(
                f"FakeModelClient queued {type(response).__name__} "
                f"but {output_model.__name__} was requested"
            )
        return response
