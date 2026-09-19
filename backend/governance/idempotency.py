"""Idempotency key derivation, argument hashing, and approval binding validation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, SecretStr


def _normalize_value(val: Any) -> Any:
    if isinstance(val, BaseModel):
        return _normalize_value(val.model_dump(mode="python"))
    if isinstance(val, Mapping):
        return {
            str(k): _normalize_value(v)
            for k, v in sorted(val.items(), key=lambda x: str(x[0]))
        }
    if isinstance(val, (Sequence, set)) and not isinstance(val, (str, bytes)):
        return [_normalize_value(item) for item in val]
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC).isoformat()
        return val.astimezone(UTC).isoformat()
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, SecretStr):
        return val.get_secret_value()
    return val


def canonical_json(data: Any) -> str:
    """Produce a deterministic, compact canonical JSON string with sorted keys."""
    normalized = _normalize_value(data)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_args_hash(validated_args: Any) -> str:
    """Compute deterministic SHA-256 hash of canonical arguments."""
    canonical = canonical_json(validated_args)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def derive_idempotency_key(tool_name: str, operation_id: str, args_hash: str) -> str:
    """Derive deterministic idempotency key from tool_name, operation_id, and args_hash."""
    seed = f"{tool_name}:{operation_id}:{args_hash}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def generate_operation_id(ticket_id: uuid.UUID | str | None = None) -> str:
    """Generate a distinct, persistent operation ID for write operations."""
    del ticket_id
    return f"op-{uuid.uuid4().hex[:12]}"


def validate_approval_binding(
    *,
    approved_operation_id: str,
    approved_args_hash: str,
    pending_operation_id: str,
    pending_args_hash: str,
) -> bool:
    """Validate that an approval record matches the pending operation exactly.

    Invariants (architecture §5.3):
        approved.operation_id == pending_operation.operation_id
        approved.args_hash == pending_operation.args_hash
    """
    if approved_operation_id != pending_operation_id:
        raise ValueError(
            f"operation_id mismatch: approved {approved_operation_id} "
            f"!= pending {pending_operation_id}"
        )
    if approved_args_hash != pending_args_hash:
        raise ValueError(
            f"args_hash mismatch: approved {approved_args_hash} != pending {pending_args_hash}"
        )
    return True
