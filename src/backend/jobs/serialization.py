"""Strict JSON (de)serialization for ARQ payloads.

ARQ defaults to pickle. The queue carries IDs only and must stay JSON, so a
compromised Redis entry can never deserialize into arbitrary Python objects.
"""

from __future__ import annotations

import json
from typing import Any


def _json_default(value: object) -> object:
    if isinstance(value, BaseException):
        # ARQ serializes job results with the same serializer as queue payloads.
        # Never persist exception text, which may contain request or provider data.
        return {"type": type(value).__name__, "message": "job failed"}
    raise TypeError(
        f"ARQ payloads must be JSON serializable, got {type(value).__name__}"
    )


def json_job_serializer(value: object) -> bytes:
    return json.dumps(
        value,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


def json_job_deserializer(payload: bytes) -> dict[str, Any]:
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("ARQ payload must decode to a JSON object")
    return decoded
