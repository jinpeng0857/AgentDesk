"""Unit tests for operation identity, idempotency key derivation, and approval invariants."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from backend.governance.idempotency import (
    canonical_json,
    compute_args_hash,
    derive_idempotency_key,
    generate_operation_id,
    validate_approval_binding,
)
from tools.schemas import ProcessRefundArgs


def test_canonical_json_is_order_independent() -> None:
    dict_a = {"b": 2, "a": 1, "c": {"y": "yes", "x": "no"}}
    dict_b = {"a": 1, "c": {"x": "no", "y": "yes"}, "b": 2}
    assert canonical_json(dict_a) == canonical_json(dict_b)
    assert compute_args_hash(dict_a) == compute_args_hash(dict_b)


def test_canonical_json_handles_decimals_consistently() -> None:
    data_a = {"amount": Decimal("200.00"), "currency": "USD"}
    data_b = {"currency": "USD", "amount": Decimal("200.00")}
    assert canonical_json(data_a) == '{"amount":"200.00","currency":"USD"}'
    assert compute_args_hash(data_a) == compute_args_hash(data_b)


def test_canonical_json_handles_pydantic_models() -> None:
    args_1 = ProcessRefundArgs(
        requester_id="usr-123",
        order_id="ord-456",
        amount=Decimal("200.00"),
        currency="USD",
        order_version=1,
    )
    args_2 = ProcessRefundArgs(
        requester_id="usr-123",
        order_id="ord-456",
        amount=Decimal("200.00"),
        currency="USD",
        order_version=1,
    )
    assert canonical_json(args_1) == canonical_json(args_2)
    assert compute_args_hash(args_1) == compute_args_hash(args_2)
    assert len(compute_args_hash(args_1)) == 64


def test_idempotency_key_strictly_derived_from_tool_operation_and_hash() -> None:
    tool_name = "process_refund"
    op_id = "op-987"
    args_hash = "a" * 64

    key = derive_idempotency_key(tool_name, op_id, args_hash)
    assert len(key) == 64
    # Stability
    assert key == derive_idempotency_key(tool_name, op_id, args_hash)

    # Different tool
    assert key != derive_idempotency_key("reset_password", op_id, args_hash)
    # Different operation_id
    assert key != derive_idempotency_key(tool_name, "op-988", args_hash)
    # Different args_hash
    assert key != derive_idempotency_key(tool_name, op_id, "b" * 64)


def test_multiple_operations_for_ticket_have_distinct_persistent_ids() -> None:
    ticket_id = uuid.uuid4()
    op_1 = generate_operation_id(ticket_id)
    op_2 = generate_operation_id(ticket_id)
    assert op_1 != op_2
    assert op_1.startswith("op-")
    assert op_2.startswith("op-")

    args_hash = compute_args_hash({"sample": "value"})
    key_1 = derive_idempotency_key("create_ticket", op_1, args_hash)
    key_2 = derive_idempotency_key("create_ticket", op_2, args_hash)
    assert key_1 != key_2


def test_validate_approval_binding_matches_exact_operation_and_hash() -> None:
    # Valid match
    assert validate_approval_binding(
        approved_operation_id="op-100",
        approved_args_hash="hash-100",
        pending_operation_id="op-100",
        pending_args_hash="hash-100",
    ) is True

    # Operation ID mismatch
    with pytest.raises(ValueError, match="operation_id mismatch"):
        validate_approval_binding(
            approved_operation_id="op-100",
            approved_args_hash="hash-100",
            pending_operation_id="op-200",
            pending_args_hash="hash-100",
        )

    # Args hash mismatch
    with pytest.raises(ValueError, match="args_hash mismatch"):
        validate_approval_binding(
            approved_operation_id="op-100",
            approved_args_hash="hash-100",
            pending_operation_id="op-100",
            pending_args_hash="hash-200",
        )
