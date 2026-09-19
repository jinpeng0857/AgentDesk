# Day 16 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: Approval 前端与三 Case 演示闭环
- Changed files:
  - frontend/src/api/client.ts
  - frontend/src/api/types.ts
  - frontend/src/router/index.ts
  - frontend/src/stores/approvals.ts
  - frontend/src/views/ApprovalView.vue
  - frontend/src/views/ApprovalView.test.ts
  - frontend/src/views/TicketsView.vue
  - scripts/l3_case_smoke.py
  - docs/evidence/l3/day-16.md
- Contract/permission/state/eval impact:
  - Approval 前端视图与 Pinia Store：
    - 交付 `frontend/src/stores/approvals.ts`：提供 `fetchApprovals`、`fetchApproval` 与 `decide` 方法，封装状态管理与统一 `ApiError` 错误码解析。
    - 交付 `frontend/src/views/ApprovalView.vue`：
      - 支持按状态过滤（pending, approved, rejected, expired, all）。
      - 详情区展示冻结执行工具名称、版本号、原因码、过期时间以及 SHA-256 参数签名哈希。
      - 密码等敏感字段严格按照后端脱敏值展示（`***`），不尝试恢复敏感明文。
      - 提供 Approve（批准并恢复工单）与 Reject（拒绝并终止请求）操作，支持可选输入决策理由。
      - 处理 409 乐观锁冲突与 TTL 过期状态，展示清晰的错误提示横幅。
    - 路由与权限隔离（`frontend/src/router/index.ts`）：
      - 注册 `/approvals` 路由，增加审批人角色守护：非 `approver` 用户访问时自动重定向回 `/tickets`。
      - 在 `TicketsView.vue` 导航栏为 `approver` 角色提供 Approvals 入口按钮。
  - 前端组件测试：
    - 交付 `frontend/src/views/ApprovalView.test.ts`（5 个 Vitest 测试全部通过）：
      - 默认加载 pending 列表并选中首个待审批项目，校验脱敏参数与哈希展示。
      - 提交 approve 决策，调用 API 并驱动视图展示已决定状态。
      - 提交 reject 决策，调用 API 并驱动视图展示已拒绝状态。
      - 处理 409 版本冲突并展示 `action-error` 横幅。
      - 对已过期状态展示过期提示并禁用决策按钮。
  - 三 Case 演示闭环脚本：
    - 交付 `scripts/l3_case_smoke.py`，完整跑通三类核心运行时路径：
      - Case A（只读知识库）：VPN SOP 查询，Supervisor -> Knowledge -> Resolved，带有完整五字段引用卡片，写工具调用严格为 0。
      - Case B（自服务低风险写）：$50 小额退款，Supervisor -> Action -> Policy 确定性自动放行（0 人工审批） -> Action execute -> Resolved，产生 1 次退款写入。
      - Case C（高风险 HITL 审批恢复）：$250 高风险退款，Supervisor -> Action -> Policy 判定高风险 -> 图在 `waiting_approval` 挂起（审批前严格 0 副作用） -> Approver 批准（版本升至 2） -> `runner.resume` 恢复执行 -> Action execute -> Resolved，退款仅执行 1 次。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check agents tools backend eval scripts` | 0 | `All checks passed!` |
| `uv run mypy agents tools backend eval scripts` | 0 | `Success: no issues found in 64 source files` |
| `uv run pytest tests/unit tests/integration -q` | 0 | `287 passed in 28.95s` |
| `npm run type-check` (in frontend) | 0 | `vue-tsc --noEmit -p tsconfig.json passed` |
| `npm run test` (in frontend) | 0 | `3 test files passed (10 tests)` |
| `npm run build` (in frontend) | 0 | `built in 153ms` |
| `uv run python scripts/l3_case_smoke.py` | 0 | `ALL THREE L3 DEMO CASES (A, B, C) PASSED END-TO-END!` |
| `git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts` | 0 | `Zero contract drift` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Case A: Read-Only Knowledge Retrieval | Resolved with complete citations (title, document_id, chunk_id, score) and 0 write tool calls | `scripts/l3_case_smoke.py::run_case_a` |
| Case B: Self-Service Low-Risk Refund | Resolved with 0 human approvals, 1 refund record, and balance decremented from $100 to $50 | `scripts/l3_case_smoke.py::run_case_b` |
| Case C: High-Risk Refund HITL & Resume | Suspended at `waiting_approval` with 0 refunds -> Approved (v2) -> Resumed -> Resolved with exactly 1 refund record | `scripts/l3_case_smoke.py::run_case_c` |
| Frontend Approval View | Full UI testing covering list, selection, frozen arguments, approve, reject, 409 conflict, and expiration | `frontend/src/views/ApprovalView.test.ts` |

## Human review

- Authorization boundary: 只有 `approver` 角色能够查看待审列表、核查未脱敏上下文与提交决策；前端导航与后端端点双重阻断未授权访问。
- Sensitive-data review: 敏感参数（密码等）严格依据后端脱敏结构以星号掩码展示，前端不发起额外请求获取明文。
- Idempotency & Concurrency: 审批操作提交版本号，并发冲突与过期判定均能友好提示且绝不导致双重执行。
- Zero Contract Drift: OpenAPI、TypeScript 生成类型与 Python Pydantic 模型完全对齐。

## Remaining gaps

- Day 16 完成。进入 Week 4 / Day 17（F-01 Tool timeout 重试、fallback 与 escalation）。
