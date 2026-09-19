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

from collections.abc import Sequence
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


class ModelTransportError(ModelClientError):
    """Transient infrastructure failure; the Worker may retry."""


class ModelOutputInvalid(ModelClientError):
    """The model did not return usable structured output."""


class ModelConfigurationError(ModelClientError):
    """Credentials, endpoint or request shape are wrong; retrying cannot help."""


RETRYABLE_ERRORS: tuple[type[ModelClientError], ...] = (ModelTransportError,)

JsonSchemaFormat = dict[str, object]
ResponseFormat = dict[str, object]


def build_response_format(output_model: type[BaseModel]) -> JsonSchemaFormat:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": output_model.__name__,
            "schema": output_model.model_json_schema(),
            "strict": True,
        },
    }


def summarize_validation_error(error: ValidationError) -> str:
    parts: list[str] = []
    for item in error.errors()[:MAX_REPORTED_ERRORS]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        parts.append(f"{location or '<root>'}: {item.get('msg', 'invalid value')}")
    return "; ".join(parts) or "invalid structure"


def mentions_unsupported_response_format(error: openai.BadRequestError) -> bool:
    text = f"{error.message} {error}".lower()
    negative = (
        "not supported" in text
        or "unsupported" in text
        or "does not support" in text
        or "unavailable" in text
    )
    return negative and ("json_schema" in text or "response_format" in text)


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
        content = await self._request(messages, config, response_format)
        try:
            return output_model.model_validate_json(content)
        except ValidationError as first_error:
            return await self._repair(
                messages,
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
            f"{config.max_retries} repair attempt(s)"
        )

    async def _request(
        self,
        messages: Sequence[ModelMessage],
        config: ModelCallConfig,
        response_format: ResponseFormat,
    ) -> str:
        payload = [
            {"role": message.role, "content": message.content} for message in messages
        ]
        try:
            completion = await self._client.chat.completions.create(
                model=config.model,
                messages=cast(Any, payload),
                temperature=config.temperature,
                response_format=cast(Any, response_format),
                timeout=config.timeout_s,
            )
        except openai.BadRequestError as exc:
            if response_format.get("type") == "json_schema" and (
                mentions_unsupported_response_format(exc)
            ):
                fallback_messages = list(messages)
                if not any("json" in m.content.lower() for m in fallback_messages):
                    if fallback_messages and fallback_messages[0].role == "system":
                        fallback_messages[0] = ModelMessage(
                            role="system",
                            content=(
                                fallback_messages[0].content
                                + "\n\nYou must respond with a valid JSON object"
                                " matching the required schema."
                            ),
                        )
                    else:
                        fallback_messages.append(
                            ModelMessage(
                                role="user",
                                content="Output your response in valid JSON format.",
                            )
                        )
                return await self._request(fallback_messages, config, {"type": "json_object"})
            raise ModelConfigurationError(
                "model provider rejected the request shape"
            ) from exc
        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            raise ModelTransportError(
                "model provider is temporarily unavailable"
            ) from exc
        except (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.NotFoundError,
        ) as exc:
            raise ModelConfigurationError(
                "model endpoint or credentials were rejected"
            ) from exc
        except openai.APIStatusError as exc:
            raise ModelConfigurationError("model provider rejected the request") from exc
        except openai.OpenAIError as exc:
            raise ModelTransportError("model request failed") from exc
        except httpx.HTTPError as exc:
            raise ModelTransportError("model transport failed") from exc

        if not completion.choices:
            raise ModelOutputInvalid("model returned no choices")
        content = completion.choices[0].message.content
        if not content:
            raise ModelOutputInvalid("model returned empty content")
        return content
