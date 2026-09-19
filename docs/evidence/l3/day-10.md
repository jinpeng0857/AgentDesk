# Day 10 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: 统一写执行网关与幂等账本 (Write Execution Gateway & Idempotency Ledger)
- Commit SHA: `38bb1bc`
- Changed files:
  - `backend/governance/executor.py`
  - `backend/db/repositories/operations.py`
  - `tests/unit/test_write_executor.py`
  - `tests/integration/test_operation_ledger.py`
  - `docs/evidence/l3/day-10.md`
- Contract/permission/state/eval impact:
  - 交付统一写操作执行网关 `WriteToolExecutor`（architecture §7.3 & §7.4），严格按不可逆流水线顺序执行：
    1. Registry Lookup：闭环校验 Tool 是否在注册表。
    2. Agent Whitelist：白名单早于任何实现解析（如 Supervisor 绝不调用任何 Tool，早于参数与适配器拦截）。
    3. Input Schema Validation：强类型校验输入参数。
    4. Deterministic Policy & Approval Gate：纯函数评估风险，高风险操作（如 >= 200.00 USD 退款）必须提供有效审批快照且严格绑定 `operation_id + args_hash`。
    5. Operation Ledger & Lease：检查 PostgreSQL 幂等账本，若已成功则直接返回 `ToolOutcome.CACHE_HIT`，零二次副作用；并发插入利用唯一约束与 Savepoint 优雅容错并竞争租约。
    6. Timeout & Retry：执行适配器并受 `asyncio.timeout(spec.timeout_s)` 保护，超时标记 `timed_out` 并审计记录。
    7. Output Schema Validation：验证适配器产出符合契约。
    8. Redaction & Audit：审计与账本摘要全量遮蔽口令、密码、密钥明文（`***`），记录全量 H-18 审计记录（`ToolAttempt`）。
  - 在 `OperationRepository` 中实现针对并发插入竞态的安全 Savepoint (`session.begin_nested()`)，优雅化解高并发并发插入同一 `idempotency_key` 时的 `UniqueViolationError`，确保单一副作用执行，其他并发调用安全读取历史结果或等待。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools backend eval scripts` | 0 | `Success: no issues found in 61 source files` |
| `uv run pytest tests/unit/test_write_executor.py -q` | 0 | `10 passed in 0.40s` |
| `uv run pytest tests/integration/test_operation_ledger.py -q` | 0 | `3 passed in 1.78s` |
| `uv run pytest tests/unit -q` | 0 | `174 passed in 2.91s` |
| `uv run pytest tests/integration -q` | 0 | `60 passed in 16.01s` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run python scripts/validate_golden_dataset.py` | 0 | `20 cases valid across 5 intents and 8 tools` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Whitelist & Unknown Tool Pre-Rejection | Supervisor refused before adapter; unknown tool refused before adapter; zero external calls | `tests/unit/test_write_executor.py` |
| Approval Required & Binding Gating | 200.00 USD refund without approval refused; mismatched approval refused; valid approval executes successfully | `tests/unit/test_write_executor.py` |
| Sensitive Data Redaction in Audit | `reset_password` temporary password masked as `***` in audit; no plaintext leakage | `tests/unit/test_write_executor.py` |
| Idempotency Cache Hit | Second execution on same pending operation hits `ToolOutcome.CACHE_HIT`; underlying service called exactly once | `tests/unit/test_write_executor.py`, `tests/integration/test_operation_ledger.py` |
| PostgreSQL Operation Ledger Race | Concurrent executions on real PostgreSQL database result in exactly 1 side effect; competing workers receive `CACHE_HIT` or `CONCURRENT_EXECUTION` | `tests/integration/test_operation_ledger.py` |

## Human review

- Gateway Invariant: Tool Registry 从不把 callable 直接交给 Agent。所有写操作执行必须通过 `WriteToolExecutor`。
- PostgreSQL as Idempotency Source of Truth: 即使 Redis 锁失效，PostgreSQL 操作账本凭借 `tool_operations.idempotency_key` 唯一键索引与事务状态保证绝对幂等。
- Redaction Integrity: 无论是 audit 日志还是账本摘要，密码与密钥均被 `redact_mapping` 强制掩码（`***`），无明文泄露。

## Remaining gaps

- Day 10 统一写执行网关与幂等账本完成。进入 Day 11（低风险 create_ticket 与 medium request_access）。
