"""Deterministic fake and the OpenAI-compatible adapter boundary."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from agents.context import ModelCallConfig, ModelMessage
from agents.schemas import Intent, KnowledgeAgentOutput, RoutingDecision, RoutingReason
from backend.integrations.model_client import (
    ModelConfigurationError,
    ModelOutputInvalid,
    ModelTransportError,
    OpenAICompatibleModelClient,
)
from tests.support.fakes.model_client import FakeModelClient

CONFIG = ModelCallConfig(model="test-model", think=False, prompt_version="1.0.0")
MESSAGES = [ModelMessage(role="user", content="VPN 无法连接")]

ROUTING_PAYLOAD = {
    "kind": "route",
    "intent": "network",
    "next_agent": "knowledge",
    "confidence": 0.95,
    "escalate": False,
    "reason_code": "needs_knowledge",
}

Handler = Callable[[httpx.Request], httpx.Response]


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def adapter(
    handler: Handler, seen: list[httpx.Request] | None = None
) -> OpenAICompatibleModelClient:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return OpenAICompatibleModelClient(
        api_key=SecretStr("test-key"),
        base_url="https://model.example.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
    )


def request_body(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content.decode("utf-8"))


# --------------------------------------------------------------------------
# Fake model client
# --------------------------------------------------------------------------


async def test_fake_returns_responses_in_declared_order() -> None:
    fake = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="knowledge",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            )
        ]
    )

    result = await fake.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    assert result.intent is Intent.NETWORK
    assert len(fake.calls) == 1
    assert fake.calls[0].output_model is RoutingDecision


async def test_fake_accepts_a_mapping_payload() -> None:
    fake = FakeModelClient([ROUTING_PAYLOAD])

    result = await fake.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    assert result.reason_code is RoutingReason.NEEDS_KNOWLEDGE


async def test_fake_raises_queued_exceptions() -> None:
    fake = FakeModelClient([ModelTransportError("provider unreachable")])

    with pytest.raises(ModelTransportError, match="provider unreachable"):
        await fake.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )


async def test_fake_exhaustion_is_deterministic() -> None:
    fake = FakeModelClient([])

    with pytest.raises(AssertionError, match="exhausted"):
        await fake.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )


async def test_fake_rejects_a_response_of_the_wrong_type() -> None:
    fake = FakeModelClient([KnowledgeAgentOutput(answer="hello", knowledge_refs=[])])

    with pytest.raises(TypeError, match="KnowledgeAgentOutput"):
        await fake.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )


# --------------------------------------------------------------------------
# OpenAI-compatible adapter
# --------------------------------------------------------------------------


async def test_adapter_sends_server_side_json_schema() -> None:
    seen: list[httpx.Request] = []
    client = adapter(lambda _request: completion(json.dumps(ROUTING_PAYLOAD)), seen)

    await client.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    body = request_body(seen[0])
    response_format = body["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["name"] == "RoutingDecision"
    assert json_schema["strict"] is True
    assert "intent" in json.dumps(json_schema["schema"])
    assert body["model"] == "test-model"
    assert body["seed"] == CONFIG.seed
    assert body["think"] is False


async def test_adapter_also_includes_exact_schema_in_system_message() -> None:
    seen: list[httpx.Request] = []
    client = adapter(lambda _request: completion(json.dumps(ROUTING_PAYLOAD)), seen)

    await client.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    body = request_body(seen[0])
    system_message = body["messages"][0]
    assert isinstance(system_message, dict)
    assert system_message["role"] == "system"
    content = system_message["content"]
    assert isinstance(content, str)
    assert '"next_agent"' in content
    assert '"reason_code"' in content
    assert "or add fields" in content


async def test_adapter_parses_structured_content() -> None:
    client = adapter(lambda _request: completion(json.dumps(ROUTING_PAYLOAD)))

    result = await client.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    assert result.intent is Intent.NETWORK
    assert result.confidence == 0.95


async def test_adapter_repairs_malformed_json_within_the_retry_budget() -> None:
    seen: list[httpx.Request] = []
    replies = iter(["not json at all", "still not json", json.dumps(ROUTING_PAYLOAD)])
    client = adapter(lambda _request: completion(next(replies)), seen)

    result = await client.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    assert result.intent is Intent.NETWORK
    # one initial request plus two repair requests (max_retries=2)
    assert len(seen) == 3


async def test_adapter_gives_up_after_the_retry_budget_without_echoing_output() -> None:
    secret_output = "leaked-model-output-that-must-not-appear"
    client = adapter(lambda _request: completion(secret_output))

    with pytest.raises(ModelOutputInvalid) as excinfo:
        await client.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )

    assert secret_output not in str(excinfo.value)
    assert "json_invalid" in str(excinfo.value)
    assert excinfo.value.output_model == "RoutingDecision"
    assert excinfo.value.validation_summary == "<root>: json_invalid"
    assert excinfo.value.repair_attempts == CONFIG.max_retries


async def test_json_object_fallback_includes_schema_in_repair_prompt() -> None:
    seen: list[httpx.Request] = []
    replies = iter(["{}", json.dumps(ROUTING_PAYLOAD)])

    def handler(request: httpx.Request) -> httpx.Response:
        body = request_body(request)
        response_format = body["response_format"]
        assert isinstance(response_format, dict)
        if response_format["type"] == "json_schema":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "json_schema conversion failed: Pattern must start with '^' "
                            "and end with '$'"
                        ),
                        "type": "invalid_request_error",
                        "param": "response_format",
                        "code": None,
                    }
                },
            )
        return completion(next(replies))

    client = adapter(handler, seen)
    await client.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    assert len(seen) == 4
    repair_body = request_body(seen[3])
    repair_messages = json.dumps(repair_body["messages"])
    assert "next_agent" in repair_messages
    assert "reason_code" in repair_messages


async def test_adapter_rejects_extra_fields_in_model_output() -> None:
    payload = {**ROUTING_PAYLOAD, "smuggled_approval": True}
    client = adapter(lambda _request: completion(json.dumps(payload)))

    with pytest.raises(ModelOutputInvalid):
        await client.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )


async def test_adapter_falls_back_to_json_object_only_when_format_is_unsupported() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request_body(request)
        response_format = body["response_format"]
        assert isinstance(response_format, dict)
        if response_format["type"] == "json_schema":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "response_format json_schema is not supported by this model",
                        "type": "invalid_request_error",
                        "param": "response_format",
                        "code": None,
                    }
                },
            )
        return completion(json.dumps(ROUTING_PAYLOAD))

    client = adapter(handler, seen)

    result = await client.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    assert result.intent is Intent.NETWORK
    assert len(seen) == 2
    second = request_body(seen[1])["response_format"]
    assert isinstance(second, dict)
    assert second["type"] == "json_object"


async def test_adapter_falls_back_when_ollama_rejects_schema_pattern_conversion() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request_body(request)
        response_format = body["response_format"]
        assert isinstance(response_format, dict)
        if response_format["type"] == "json_schema":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "json_schema conversion failed: Pattern must start with '^' "
                            "and end with '$'"
                        ),
                        "type": "invalid_request_error",
                        "param": "response_format",
                        "code": None,
                    }
                },
            )
        return completion(json.dumps(ROUTING_PAYLOAD))

    client = adapter(handler, seen)

    result = await client.complete_structured(
        messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
    )

    assert result.intent is Intent.NETWORK
    assert len(seen) == 2
    second = request_body(seen[1])["response_format"]
    assert isinstance(second, dict)
    assert second["type"] == "json_object"


async def test_adapter_does_not_fall_back_on_an_unrelated_bad_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "unsupported model",
                    "type": "invalid_request_error",
                    "param": "model",
                    "code": None,
                }
            },
        )

    client = adapter(handler)

    with pytest.raises(ModelConfigurationError):
        await client.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )


async def test_adapter_classifies_timeouts_as_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = adapter(handler)

    with pytest.raises(ModelTransportError):
        await client.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )


async def test_adapter_classifies_server_errors_as_retryable() -> None:
    client = adapter(lambda _request: httpx.Response(503, json={"error": "unavailable"}))

    with pytest.raises(ModelTransportError):
        await client.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )


async def test_adapter_classifies_authentication_failure_as_non_retryable() -> None:
    client = adapter(
        lambda _request: httpx.Response(
            401,
            json={
                "error": {
                    "message": "invalid api key",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )
    )

    with pytest.raises(ModelConfigurationError):
        await client.complete_structured(
            messages=MESSAGES, output_model=RoutingDecision, config=CONFIG
        )


def test_only_transport_errors_are_retryable() -> None:
    from backend.integrations import model_client

    assert (ModelTransportError,) == model_client.RETRYABLE_ERRORS
