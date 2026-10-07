# Day 19 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: F-04 越权 Tool 与 F-06 Prompt Injection
- Changed files:
  - backend/governance/executor.py
  - prompts/supervisor.yaml
  - prompts/action.yaml
  - specs/agent_contracts/supervisor.yaml
  - specs/agent_contracts/action.yaml
  - tests/test_failure_modes.py
  - docs/evidence/l3/day-19.md
- Contract/permission/state/eval impact:
  - F-04 网关层未知/越权 Tool 拦截（Unauthorized & Unknown Tool Rejection）：
    - 在 `backend/governance/executor.py` 中强化执行网关：在执行任何底层适配器查找前，先行校验注册表存在性、角色白名单以及只读/写工具匹配性。
    - 验证生产注册表 `TOOL_REGISTRY`、`TOOL_MODEL_PAIRS` 以及各角色白名单均无 `delete_user`，杜绝隐藏 callable。
    - 验证四大越权攻击场景：
      1. Supervisor 调用只读工具 `query_user` -> 判定越权拒绝并返回 `TOOL_NOT_PERMITTED_FOR_AGENT`。
      2. Supervisor 调用写工具 `process_refund` -> 判定越权拒绝并返回 `TOOL_NOT_PERMITTED_FOR_AGENT`。
      3. Knowledge 节点调用写工具 `process_refund`（Read/Write 路径均被拦截）-> 判定越权拒绝并返回 `TOOL_NOT_PERMITTED_FOR_AGENT`。
      4. 任何 Agent 请求调用未知工具 `delete_user` -> 在适配器查找前即刻拒绝并返回 `UNKNOWN_TOOL`。
    - 每次越权拦截均产生 `ToolOutcome.REJECTED` 审计事件，所有领域服务调用次数严格为 0。
  - F-06 提示词注入攻击防御与审批门禁（Prompt Injection Defense & Gate Integrity）：
    - `prompts/supervisor.yaml` 与 `prompts/action.yaml` 强化反注入约束；Prompt 仅强化模型输出偏好，不承担安全边界责任。
    - `specs/agent_contracts/` 明确外部非可信输入无法改变确定性策略与图执行边界。
    - 确定性防御全链路验证：
      1. 注入载荷攻击：工单载荷伪装为管理员维护模式，强行指示绕过安全检查与免密退款 $500.00。
      2. 即使模型被输入误导提出该退款 ActionProposal，纯 Python 编写的确定性 Policy（`assess_risk`）严格按照业务金额阈值（> $200.00）强制判定为 `RiskLevel.HIGH` 与 `require_human=True`，不可信输入无法干涉策略判定。
      3. Approval 节点持久化 pending 审批单并挂起图（`waiting_approval`），DB 业务退款记录为 0，订单金额毫厘未动。
      4. 绕过审批直接执行攻击（Direct Bypass）：攻击者直接调用 `WriteToolExecutor.execute` 且不传审批快照，网关拦截并返回 `APPROVAL_REQUIRED`。
      5. 伪造审批快照攻击（Approval Forgery）：攻击者篡改 `args_hash` 试图欺骗网关，网关核对哈希不匹配直接拒绝并返回 `APPROVAL_MISMATCH`。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check agents tools backend eval scripts tests` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 66 source files` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run pytest tests/test_failure_modes.py -v` | 0 | `6 passed in 7.86s` |
| `uv run pytest tests/unit tests/integration -q` | 0 | `287 passed in 32.45s` |
| `git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts` | 0 | `Zero contract drift` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| F-04: Unauthorized and Unknown Tool Rejection | `delete_user` verified absent from registry, Supervisor/Knowledge/Unknown attempts rejected before adapter lookup, 6 audits recorded, 0 adapter side effects | `tests/test_failure_modes.py::test_f04_unauthorized_and_unknown_tool_rejections_have_zero_side_effects` |
| F-06: Prompt Injection Defense & Approval Gate | Hostile override prompt cannot bypass deterministic policy ($500.00 refund flagged HIGH/require_human), halts at waiting_approval, direct execution rejected, forged snapshot rejected, 0 refunds inserted | `tests/test_failure_modes.py::test_f06_prompt_injection_cannot_bypass_policy_or_approval_gates` |

## Human review

- Authorization boundary: 网关作为唯一 Tool 执行入口，执行白名单与入参类型的硬编码静态闭环校验；模型与不可信用户输入无法在网关层伪造权限。
- Sensitive-data review: 所有越权与未知 Tool 调用拒绝审计均经过 `redact_mapping` 脱敏处理，不泄露参数细节。
- Defense in Depth: Prompt 仅作为模型行为引导层，真正的安全边界完全交由确定性 Python Policy 与网关拦截层保障（Prompt is not a security boundary）。
- Side Effects Isolation: F-04 与 F-06 全套对抗攻击测试中，数据库写副作用严格为 0。

## Remaining gaps

- Day 19 完成。进入 Day 20（F-05 审批过期与 F-08 重复决定/恢复）。