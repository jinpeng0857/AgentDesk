"""OpenAI-compatible model boundary.

Agents never instantiate a provider SDK. This adapter is the only place that
talks to a model endpoint, and it classifies every failure so the Worker knows
what may be retried:

* :class:`ModelTransportError` 鈥?transient infrastructure, retryable.
* :class:`ModelOutputInvalid` 鈥?the model could not produce valid structured
  output, not retryable at the queue level.
* :class:`ModelConfigurationError` 鈥?credentials, endpoint or request shape,
  not retryable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, TypeVar, cast

import httpx
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from agents.context import ModelCallConfig, ModelMessage
from backend.config import AppSettings

ModelT = TypeVar("ModelT", bound=BaseModel)

MAX_REPAIR_ECHO_CHARS = 2_000
MAX_REPORTED_ERRORS = 10


class ModelClientError(RuntimeError):
    """Base class for every classified model failure."""


class ModelTransportFailureKind(StrEnum):
    """Safe, stable categories for retryable model-provider failures."""

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"


class ModelTransportError(ModelClientError):
    """Retryable provider failure with non-sensitive diagnostic metadata."""

    def __init__(
        self,
        message: str,
        *,
        kind: ModelTransportFailureKind = ModelTransportFailureKind.UNKNOWN,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


class ModelOutputInvalid(ModelClientError):
    """The model did not return usable structured output."""

    def __init__(
        self,
        message: str,
        *,
        output_model: str = "unknown",
        validation_summary: str = "invalid structure",
        repair_attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.output_model = output_model
        self.validation_summary = validation_summary
        self.repair_attempts = repair_attempts


class ModelConfigurationError(ModelClientError):
    """Credentials, endpoint or request shape are wrong; retrying cannot help."""


RETRYABLE_ERRORS: tuple[type[ModelClientError], ...] = (ModelTransportError,)

JsonSchemaFormat = dict[str, object]
ResponseFormat = dict[str, object]


def build_response_format(output_model: type[BaseModel]) -> JsonSchemaFormat:
    schema = output_model.model_json_schema()

    def remove_unsupported_regex_patterns(value: object) -> None:
        """Keep Pydantic validation local when a provider grammar cannot parse look-arounds."""
        if isinstance(value, dict):
            pattern = value.get("pattern")
            has_lookaround = isinstance(pattern, str) and any(
                marker in pattern for marker in ("(?=", "(?!", "(?<")
            )
            if has_lookaround:
                value.pop("pattern")
            properties = value.get("properties")
            kind_schema = properties.get("kind") if isinstance(properties, dict) else None
            required = value.get("required")
            if (
                isinstance(kind_schema, dict)
                and "const" in kind_schema
                and isinstance(required, list)
                and "kind" not in required
            ):
                # Pydantic defaults make discriminator fields optional in its
                # JSON Schema, but the Action union cannot validate without them.
                required.append("kind")
            for item in value.values():
                remove_unsupported_regex_patterns(item)
        elif isinstance(value, list):
            for item in value:
                remove_unsupported_regex_patterns(item)

    remove_unsupported_regex_patterns(schema)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": output_model.__name__,
            "schema": schema,
            "strict": True,
        },
    }


def summarize_validation_error(error: ValidationError) -> str:
    parts: list[str] = []
    for item in error.errors()[:MAX_REPORTED_ERRORS]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        err_category = item.get("type", "invalid_value")
        parts.append(f"{location or '<root>'}: {err_category}")
    return "; ".join(parts) or "invalid structure"


def mentions_unsupported_response_format(error: openai.BadRequestError) -> bool:
    text = f"{error.message} {error}".lower()
    schema_conversion_failed = (
        "json_schema conversion failed" in text or "json schema conversion failed" in text
    )
    if schema_conversion_failed:
        return True

    # vLLM's grammar backend rejects Pydantic Decimal patterns that contain
    # look-around assertions (for example, max_digits/decimal_places). Keep
    # the application schema in the prompt and validate its output locally.
    unsupported_grammar_regex = (
        "grammar error" in text
        and "regex parse error" in text
        and "look-around" in text
        and "not supported" in text
    )
    if unsupported_grammar_regex:
        return True

    negative = (
        "not supported" in text
        or "unsupported" in text
        or "does not support" in text
        or "unavailable" in text
    )
    return negative and ("json_schema" in text or "response_format" in text)


def include_json_schema_instruction(
    messages: Sequence[ModelMessage], response_format: ResponseFormat
) -> list[ModelMessage]:
    """Keep the model-specific output contract when the provider only accepts JSON mode."""
    schema_info = response_format.get("json_schema")
    if not isinstance(schema_info, dict):
        return list(messages)
    schema = schema_info.get("schema")
    if not isinstance(schema, dict):
        return list(messages)

    instruction = (
        "You must return one JSON object that validates exactly against this schema; "
        "do not omit required fields or add fields:\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )
    result = list(messages)
    if result and result[0].role == "system":
        schema_instruction_marker = (
            "You must return one JSON object that validates exactly against this schema"
        )
        if schema_instruction_marker in result[0].content:
            return result
        result[0] = ModelMessage(
            role="system", content=f"{result[0].content}\n\n{instruction}"
        )
    else:
        result.insert(0, ModelMessage(role="system", content=instruction))
    return result


class OpenAICompatibleModelClient:
    """Implements :class:`agents.context.ModelClient` over an OpenAI-compatible API."""

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value() if api_key is not None else "not-required",
            base_url=base_url,
            max_retries=0,
            http_client=http_client,
        )

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> OpenAICompatibleModelClient:
        return cls(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            http_client=http_client,
        )

    async def complete_structured(
        self,
        *,
        messages: Sequence[ModelMessage],
        output_model: type[ModelT],
        config: ModelCallConfig,
    ) -> ModelT:
        response_format = build_response_format(output_model)
        # The provider already receives the schema in response_format. Repeating
        # the full JSON schema in the prompt wastes context and can push Action's
        # tool catalogue beyond the model's context window. The fallback path
        # adds the schema to the prompt only when JSON mode requires it.
        request_messages = list(messages)
        content = await self._request(request_messages, config, response_format)
        try:
            return output_model.model_validate_json(content)
        except ValidationError as first_error:
            return await self._repair(
                request_messages,
                output_model,
                config,
                response_format,
                content,
                summarize_validation_error(first_error),
            )

    async def _repair(
        self,
        messages: Sequence[ModelMessage],
        output_model: type[ModelT],
        config: ModelCallConfig,
        response_format: ResponseFormat,
        content: str,
        errors: str,
    ) -> ModelT:
        for _attempt in range(config.max_retries):
            repair_messages = [
                *messages,
                ModelMessage(
                    role="assistant",
                    content=content[:MAX_REPAIR_ECHO_CHARS] or "(empty)",
                ),
                ModelMessage(
                    role="user",
                    content=(
                        "Your previous reply did not satisfy the required JSON schema. "
                        f"Validation errors: {errors}. Reply with JSON only."
                    ),
                ),
            ]
            content = await self._request(repair_messages, config, response_format)
            try:
                return output_model.model_validate_json(content)
            except ValidationError as error:
                errors = summarize_validation_error(error)

        raise ModelOutputInvalid(
            f"model did not return valid {output_model.__name__} JSON after "
            f"{config.max_retries} repair attempt(s): {errors}",
            output_model=output_model.__name__,
            validation_summary=errors,
            repair_attempts=config.max_retries,
        )

    async def _request(
        self,
        messages: Sequence[ModelMessage],
        config: ModelCallConfig,
        response_format: ResponseFormat,
    ) -> str:
        payload = [{"role": message.role, "content": message.content} for message in messages]
        try:
            request_options: dict[str, Any] = {
                "model": config.model,
                "messages": cast(Any, payload),
                "temperature": config.temperature,
                "seed": config.seed,
                "response_format": cast(Any, response_format),
                "timeout": config.timeout_s,
            }
            if config.think is not None:
                # Ollama exposes this OpenAI-compatible extension to disable
                # hidden Qwen reasoning for classification/evaluation calls.
                request_options["extra_body"] = {"think": config.think}
            completion = await self._client.chat.completions.create(**request_options)
        except openai.BadRequestError as exc:
            if response_format.get("type") == "json_schema" and (
                mentions_unsupported_response_format(exc)
            ):
                fallback_messages = include_json_schema_instruction(messages, response_format)
                return await self._request(fallback_messages, config, {"type": "json_object"})
            raise ModelConfigurationError("model provider rejected the request shape") from exc
        except (openai.APITimeoutError, httpx.TimeoutException) as exc:
            raise ModelTransportError(
                "model provider request timed out",
                kind=ModelTransportFailureKind.TIMEOUT,
            ) from exc
        except (openai.APIConnectionError, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ModelTransportError(
                "model provider connection failed",
                kind=ModelTransportFailureKind.CONNECTION,
            ) from exc
        except (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.NotFoundError,
        ) as exc:
            raise ModelConfigurationError("model endpoint or credentials were rejected") from exc
        except openai.APIStatusError as exc:
            if exc.status_code == 429 or 500 <= exc.status_code <= 599:
                raise ModelTransportError(
                    "model provider returned a retryable server status",
                    kind=ModelTransportFailureKind.SERVER_ERROR,
                    status_code=exc.status_code,
                ) from exc
            raise ModelConfigurationError("model provider rejected the request") from exc
        except openai.OpenAIError as exc:
            raise ModelConfigurationError("model SDK rejected the request") from exc
        except httpx.HTTPError as exc:
            raise ModelTransportError(
                "model provider connection failed",
                kind=ModelTransportFailureKind.CONNECTION,
            ) from exc

        if not completion.choices:
            raise ModelOutputInvalid("model returned no choices")
        content = cast(str | None, completion.choices[0].message.content)
        if not content:
            raise ModelOutputInvalid("model returned empty content")
        return content
