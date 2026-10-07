# Day 11 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: 低风险 create_ticket 与 medium request_access
- Commit SHA: `5e560b2`
- Changed files:
  - `tools/ticket.py`
  - `tools/access.py`
  - `backend/integrations/tool_adapters.py`
  - `tests/unit/test_write_tools.py`
  - `tests/integration/test_write_adapters.py`
  - `docs/evidence/l3/day-11.md`
- Contract/permission/state/eval impact:
  - 交付领域层写能力：
    - `tools/ticket.py`：实现 `create_ticket(args, service)`，调用 `TicketService` 协议创建下游排查工单。
    - `tools/access.py`：实现 `request_access(args, service)`，调用 `AccessRequestService` 协议创建权限申请。
    - 两者严格遵循架构依赖边界约束，仅依赖 `tools.context`，零依赖 `backend`、数据库或外部框架。
  - 交付 PostgreSQL 持久化适配器（`backend/integrations/tool_adapters.py`）：
    - `SqlTicketService`：向 `downstream_tickets` 表写入记录，生成 `dt-...` 标识，状态为 `created`。
    - `SqlAccessRequestService`：向 `access_requests` 表写入申请记录，生成 `ar-...` 标识，状态为 `created`。遵循安全架构原则：此工具仅记录权限申请事实，绝不直接赋权或提升权限。
  - 经由统一写执行网关 `WriteToolExecutor` 闭环验证：
    - 本人发起 `create_ticket`（低风险自服务）和 `request_access`（中风险自服务）均在无人工介入下安全执行并落库。
    - 跨用户越权申请被 Policy 确定性拒绝（`DENY_NOT_OWNER`），底层适配器零调用，业务表记录增量严格为 0。
    - 幂等账本校验：对同一 PendingOperation 重复调用，第二次自动命中 `CACHE_HIT`，业务表行数严格保持 1。
    - 全链路可观测性：返回结果、PostgreSQL 操作账本（`tool_operations`）与审计流水（`tool_calls`）通过 `request_id`、`ticket_id`、`trace_id` 与 `operation_id` 实现四重无缝串联。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools backend eval scripts` | 0 | `Success: no issues found in 62 source files` |
| `uv run pytest tests/unit/test_write_tools.py -q` | 0 | `8 passed in 0.37s` |
| `uv run pytest tests/integration/test_write_adapters.py -q` | 0 | `4 passed in 1.90s` |
| `uv run pytest tests/unit/test_dependency_boundaries.py -q` | 0 | `5 passed in 0.11s` |
| `uv run pytest tests/unit -q` | 0 | `182 passed in 3.10s` |
| `uv run pytest tests/integration -q` | 0 | `64 passed in 16.77s` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run python scripts/validate_golden_dataset.py` | 0 | `20 cases valid across 5 intents and 8 tools` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Downstream Ticket Creation | Created in PostgreSQL `downstream_tickets` with `status="created"`, linked to source ticket | `tests/integration/test_write_adapters.py` |
| Access Request Non-Escalation | Created in PostgreSQL `access_requests` with `status="created"`; never mutates user role/permissions | `tests/integration/test_write_adapters.py` |
| Cross-User Write Rejection | Cross-user ticket & access requests denied with `DENY_NOT_OWNER`; DB rows in business tables remain 0 | `tests/unit/test_write_tools.py`, `tests/integration/test_write_adapters.py` |
| Write Gateway Idempotency | Duplicate call yields `CACHE_HIT`; database row count remains strictly 1 | `tests/integration/test_write_adapters.py` |
| Trace & Audit Correlation | Result, `tool_operations`, and `tool_calls` correlated via `request_id`, `ticket_id`, `trace_id`, `operation_id` | `tests/integration/test_write_adapters.py` |

## Human review

- Non-Granting Invariant: 确认 `request_access` 绝不执行 `UPDATE users SET role = ...` 或直接修改用户权限，仅生成待审批的访问申请记录。
- Self-Service Policy: 本人申请在 Policy 中判定为允许，越权操作直接在 Gateway 前置拦截。
- Idempotency & DB Cleanliness: 验证重复执行不仅返回相同结果，且业务表物理行数严格保持单条，杜绝重复工单或权限申请冗余。

## Remaining gaps

- Day 11 完成。进入 Day 12（Password Reset Skill 与敏感结果保护）。
