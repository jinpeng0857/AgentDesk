# Day 18 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: F-02 非法模型结构与 F-03 图循环（15 步硬上限熔断）
- Changed files:
  - backend/integrations/model_client.py
  - backend/graph/edges.py
  - backend/graph/nodes.py
  - tests/fakes/model_client.py
  - tests/test_failure_modes.py
  - docs/evidence/l3/day-18.md
- Contract/permission/state/eval impact:
  - F-02 非法模型结构防穿透与敏感词脱敏（Model Output Validation & Redaction）：
    - 在 `backend/integrations/model_client.py` 中更新 `summarize_validation_error`：仅提取验证错误类别（`type`）与字段位置（`loc`），坚决剥离包含用户输入或原始生成片段的 `input` 与 `ctx` 字段，杜绝模型非法生成物中的敏感凭据（例如密码、Token 等）在错误摘要中回显。
    - `FakeModelClient` 在验证失败时执行修复尝试（Repair Loop），两次输出非法结构后触发 `ModelOutputInvalid` 异常。
    - `backend/graph/nodes.py` 中 `make_supervisor_node` 捕获 `ModelOutputInvalid`，将 `routing_decision` 置为 `None`，`route_from_supervisor` 判定无效决策后平滑流转至人工升级节点（`escalate`），确保非法结构绝不穿透至下游状态机或工具层，副作用严格为 0。
  - F-03 图循环硬上限熔断与审计完整性（Graph Loop 15-Step Cap）：
    - `backend/graph/edges.py` 中 `advance_dispatch` 在第 15 步（`step_count >= 15`）时强制将状态切换至 `TicketStatus.ESCALATED`。
    - 允许 `dispatcher` 的 `escalate_ticket` 动作在当前已处于 `escalated` 状态时安全返回，防止不可变终态保护阻断升级收敛。
    - 构造 Supervisor 与 Knowledge 双向死循环场景，验证 LangGraph 在第 15 步精确熔断停机，`agent_history` 中记录恰好 15 步轨迹，绝对不触发第 16 步模型调用或 Tool 执行。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check agents tools backend eval scripts tests` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 66 source files` |
| `uv run pytest tests/test_failure_modes.py -v` | 0 | `4 passed in 7.13s` |
| `uv run pytest tests/unit tests/integration -q` | 0 | `287 passed in 33.40s` |
| `git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts` | 0 | `Zero contract drift` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| F-02: Invalid Model Structure Escalation | Validation error sanitized (no secret leak), 2 failed repairs raise ModelOutputInvalid, supervisor safely catches and routes to escalated, 0 tool side effects | `tests/test_failure_modes.py::test_f02_invalid_model_structure_escalates_after_failed_repairs` |
| F-03: Graph Loop 15-Step Cap & Escalation | Ping-pong loop halted exactly at step 15, escalated terminal state, agent_history has exactly 15 records, step 16 never called | `tests/test_failure_modes.py::test_f03_graph_loop_halts_at_max_steps_and_escalates` |

## Human review

- Authorization boundary: 模型结构非法直接熔断并转交人工升级，模型越权或伪造输出无法越过校验网关。
- Sensitive-data review: 错误摘要清洗掉所有敏感回显（测试用例显式断言 `SuperSecretPass123!` 不在错误摘要与下游日志中），遵循最小信息披露原则。
- Graph Safety & Finite Termination: 严格保证所有图执行至多 15 步（`MAX_STEPS = 15`），无论模型生成任何死循环路由均保证有界终止。
- Side Effects Isolation: 结构解析失败和循环阻断全过程，数据库业务副作用次数严格为 0。

## Remaining gaps

- Day 18 完成。进入 Day 19（F-04 越权 Tool 与 F-06 Prompt Injection）。