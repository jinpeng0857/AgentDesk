# Day 09 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: 确定性 Policy 风险矩阵与授权规则
- Commit SHA: `ef636f0`
- Changed files:
  - `agents/policy.py`
  - `agents/action.py`
  - `agents/idempotency.py`
  - `backend/governance/idempotency.py`
  - `prompts/action.yaml`
  - `prompts/policy.yaml`
  - `specs/agent_contracts/action.yaml`
  - `specs/agent_contracts/policy.yaml`
  - `tests/unit/test_policy.py`
  - `tests/unit/test_agents.py`
  - `docs/evidence/l3/day-09.md`
- Contract/permission/state/eval impact:
  - 交付纯函数 `assess_risk(operation, *, actor, resource)`，实现确定性风险判定矩阵与授权规则（architecture §7.4）：
    - `read Tool`：低风险，自动允许（`PolicyReason.ALLOW_READ`，`require_human=False`）。
    - `create_ticket`：低风险，自动允许（`PolicyReason.ALLOW_SELF_SERVICE`，`require_human=False`）。
    - `request_access`：仅创建权限申请，不直接授权，中风险，自动允许（`PolicyReason.ALLOW_SELF_SERVICE`，`require_human=False`）。
    - `reset_password`：本人且活跃账号判定为中风险自动允许；非本人判定为越权拒绝（`DENY_NOT_OWNER`）；非活跃账号拒绝（`DENY_INACTIVE_ACCOUNT`）。
    - `process_refund`：精确以 `Decimal("200.00")` 为界：199.99 USD 为中风险自动允许（`ALLOW_LOW_REFUND`，`require_human=False`）；200.00 USD 及以上达到高风险阈值，强制触发审批（`REQUIRES_REFUND_APPROVAL`，`require_human=True`）；非本人订单严格拒绝（`DENY_NOT_OWNER`）。
    - 未知/越权工具与未认证请求者：确定性拒绝（`DENY_UNKNOWN_TOOL` / `DENY_UNAUTHORIZED`），零外部副作用。
  - 决策完全由 Python 纯函数闭环，LLM 不介入 allow/deny、风险级别判定或审批必要性裁决；模型仅允许在判定后生成润色解释且不得篡改任何判定字段。
  - 在 `agents/action.py` 实现确定性工厂 `create_pending_operation`：仅由包装代码生成 `operation_id`、SHA-256 `args_hash` 与 `side_effects="write"`，模型输出中不得包含这些受保护字段。
  - 坚守清晰分层架构：将参数规范化、SHA-256 派生与操作唯一标识置于领域层 `agents/idempotency.py`，确保 `agents` 领域层零依赖 `backend` 基础设施，完美通过 `test_domain_layers_do_not_import_service_or_infrastructure` 架构边界测试。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 63 source files` |
| `uv run pytest tests/unit/test_policy.py -q` | 0 | `15 passed in 0.09s` |
| `uv run pytest tests/unit/test_agents.py -q` | 0 | `20 passed in 0.10s` |
| `uv run pytest tests/unit/test_dependency_boundaries.py -q` | 0 | `5 passed in 0.07s` |
| `uv run pytest tests/unit -q` | 0 | `164 passed in 2.95s` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run python scripts/validate_golden_dataset.py` | 0 | `20 cases valid across 5 intents and 8 tools` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Refund Risk Threshold Matrix | 199.99 USD -> medium risk (automated); 200.00 USD -> high risk (require_human=True); non-owner order -> denied | `tests/unit/test_policy.py` |
| Self-Service & Privilege Escalation Gating | Self password reset on active account allowed; inactive account denied; cross-user reset/ticket/refund strictly denied with zero side-effects | `tests/unit/test_policy.py` |
| Domain Layer Clean Isolation | Strict zero-import of backend/fastapi/sqlalchemy from agents/tools/skills | `tests/unit/test_dependency_boundaries.py` |

## Human review

- Authorization boundary: 确认 `allow/deny`、最终风险等级及 `require_human` 标志 100% 由纯函数规则产生，LLM 绝无权力放行高风险操作或绕过人工审批。
- Prompt constraint review: `prompts/action.yaml` 与 `prompts/policy.yaml` 严格遵循绝对约束原则，包含 `must not`、`never`、`only`，Prompt 严禁越权修改治理结论。
- Numeric precision: 退款金额全链路采用 `Decimal`，199.99 USD 与 200.00 USD 边界断言无浮点误差。

## Remaining gaps

- Day 09 Policy 确定性风险矩阵完成。进入 Day 10（统一写执行网关与幂等账本）。
