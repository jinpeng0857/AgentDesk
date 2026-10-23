"""ARQ payload serialization must be strict JSON, never pickle."""

from __future__ import annotations

import json

import pytest
from arq.jobs import serialize_result

from backend.jobs.serialization import json_job_deserializer, json_job_serializer


def test_arq_payload_is_json_and_contains_only_ticket_id() -> None:
    payload = json_job_serializer(
        {"f": "start_ticket", "a": ("ticket-123",), "k": {}, "t": None, "et": 1}
    )

    assert payload.startswith(b"{")
    assert b"pickle" not in payload
    assert json_job_deserializer(payload)["a"] == ["ticket-123"]


def test_round_trip_preserves_scalar_values() -> None:
    original = {"f": "start_ticket", "a": ["ticket-9"], "k": {}, "t": None, "et": 3}

    assert json_job_deserializer(json_job_serializer(original)) == original


def test_serializer_rejects_unsupported_objects_instead_of_stringifying() -> None:
    class Unsupported:
        pass

    with pytest.raises(TypeError, match="JSON serializable"):
        json_job_serializer({"f": "start_ticket", "a": (Unsupported(),)})


def test_serializer_preserves_safe_exception_type_without_leaking_message() -> None:
    from backend.integrations.model_client import ModelOutputInvalid

    payload = json_job_serializer(
        {
            "f": "start_ticket",
            "s": False,
            "r": ModelOutputInvalid("sensitive model response must not be exposed"),
        }
    )

    result = json_job_deserializer(payload)
    assert result["r"] == {
        "type": "ModelOutputInvalid",
        "message": "job failed",
    }
    assert b"sensitive model response" not in payload


def test_arq_can_store_failed_job_result_with_sanitized_exception_type() -> None:
    result = serialize_result(
        "start_ticket",
        ("ticket-123",),
        {},
        1,
        100,
        False,
        RuntimeError("private provider response"),
        110,
        120,
        "job-ref",
        "arq:queue",
        "start:ticket-123",
        serializer=json_job_serializer,
    )

    assert result is not None
    decoded = json_job_deserializer(result)
    assert decoded["s"] is False
    assert decoded["r"] == {"type": "RuntimeError", "message": "job failed"}
    assert b"private provider response" not in result


def test_serializer_rejects_non_finite_floats() -> None:
    with pytest.raises(ValueError, match="Out of range float"):
        json_job_serializer({"t": float("nan")})


def test_deserializer_requires_a_json_object() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        json_job_deserializer(b"[1, 2, 3]")


def test_payload_is_valid_utf8_json() -> None:
    payload = json_job_serializer({"f": "start_ticket", "a": ["t-1"]})

    assert json.loads(payload.decode("utf-8"))["f"] == "start_ticket"
