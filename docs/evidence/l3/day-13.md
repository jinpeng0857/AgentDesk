# Day 13 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: Refund 写 Tool 与金额边界
- Commit SHA: `4a8cc39`
- Changed files:
  - `tools/ticket.py`
  - `backend/integrations/tool_adapters.py`
  - `tests/unit/test_refund_policy.py`
  - `tests/integration/test_refund_execution.py`
  - `tests/unit/test_write_tools.py`
  - `docs/evidence/l3/day-13.md`
- Contract/permission/state/eval impact:
  - 交付退款领域工具与 PostgreSQL 持久化适配器：
    - `tools/ticket.py`：实现 `process_refund(args, service)` 领域工具函数，依赖 `RefundService` 协议，严格遵循架构分层约束。
    - `backend/integrations/tool_adapters.py`：实现 `SqlRefundService`，对接 PostgreSQL `orders` 与 `refunds` 表。
  - 金额边界与风险策略（AGENTS.md & Architecture §7.4）：
    - 全链路使用 `Decimal`，禁止 `float`。
    - 阈值边界：金额 `< 200.00 USD`（如 199.99 USD）判定为 `RiskLevel.MEDIUM`，`require_human=False`，自动执行。
    - 阈值边界：金额 `>= 200.00 USD`（如 200.00 USD 或更高）判定为 `RiskLevel.HIGH`，`require_human=True`，无有效人工审批时由网关坚决拒绝（`ERROR_APPROVAL_REQUIRED`），外部副作用计数严格为 0。
    - 跨用户所有权校验：请求者不匹配订单所有者时直接返回 `DENY_NOT_OWNER`。
  - 订单状态、版本与可退金额守卫：
    - 订单状态非 `paid`（如 `refunded`、`cancelled`、`pending`）时拒绝退款。
    - 订单版本不匹配（`order_version != order.version`）时拒绝退款。
    - 申请金额超出剩余可退金额（`amount > order.refundable_amount`）时拒绝退款。
    - 退款成功后原子扣减 `order.refundable_amount`，可退金额降至 0 时将订单状态更新为 `refunded`，并递增 `order.version`。
  - 审批与幂等账本（Operation Ledger）约束：
    - 当订单版本或参数哈希变动时，旧审批记录不可复用（`ERROR_APPROVAL_MISMATCH`）。
    - 重复调用同一 `operation_id` 返回 `ToolOutcome.CACHE_HIT`，业务表记录数严格保持 1，杜绝二次退款副作用。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy --python-version 3.12 agents tools backend eval scripts tests/unit/test_refund_policy.py tests/integration/test_refund_execution.py` | 0 | `Success: no issues found in 65 source files` |
| `uv run pytest tests/unit/test_refund_policy.py -q` | 0 | `11 passed in 0.66s` |
| `uv run pytest tests/integration/test_refund_execution.py -q` | 0 | `6 passed in 4.20s` |
| `uv run pytest tests/unit/test_dependency_boundaries.py -q` | 0 | `5 passed in 0.11s` |
| `uv run pytest tests/unit -q` | 0 | `200 passed in 6.84s` |
| `uv run pytest tests/integration -q` | 0 | `73 passed in 29.57s` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run python scripts/validate_golden_dataset.py` | 0 | `20 cases valid across 5 intents and 8 tools` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Medium Risk (199.99 USD) Auto Execution | Automatically executed without approval; 1 refund row inserted, order decremented | `tests/integration/test_refund_execution.py` |
| High Risk (200.00 USD) Blocked Without Approval | Rejected with `ERROR_APPROVAL_REQUIRED`; DB refund count strictly 0, order unmodified | `tests/integration/test_refund_execution.py` |
| High Risk (200.00 USD) Allowed With Approval | Executed successfully with valid approval snapshot; 1 refund row inserted | `tests/integration/test_refund_execution.py` |
| Approval Mismatch on Version/Args Mutation | Mutated order_version fails closed with `ERROR_APPROVAL_MISMATCH`; 0 side effects | `tests/unit/test_refund_policy.py` |
| State & Ownership Validation | Unowned order, non-paid order, version mismatch, or excess amount rejected | `tests/integration/test_refund_execution.py` |
| Ledger Idempotency & Zero Double Refund | Replay hits `CACHE_HIT`; database refund rows strictly 1, order balance debited once | `tests/integration/test_refund_execution.py` |

## Human review

- Decimal Precision: 确认金额全链路使用 `Decimal`，无任何 `float` 精度丢失隐患。
- 199.99 vs 200.00 Boundary: 确认策略阈值严格满足 `>= 200.00 USD` 需要人工审批，`199.99 USD` 作为自服务中风险自动放行。
- Zero Side Effect on Rejection: 确认 200.00 USD 在未获得审批时，不仅网关返回拒绝，且 PostgreSQL 中 orders 表未发生金额变动，refunds 表无脏数据生成。

## Remaining gaps

- Day 13 完成。进入 Day 14（Approval API、乐观锁与 TTL）。
