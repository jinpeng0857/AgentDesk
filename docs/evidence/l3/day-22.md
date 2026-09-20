# Day 22 Acceptance

- Date (UTC+8): 2026-09-20
- Goal: Phase 2 总门禁与 live 行为对比（Gate B）
- Changed files:
  - eval/artifacts/phase2-after.json
  - README.md
  - docs/architecture.md
  - PORTFOLIO.md
  - docs/evidence/l3/day-22.md
- Contract/permission/state/eval impact:
  - Phase 2 总门禁（Gate B: 治理与 HITL 闭环）全量达标：
    - 四大业务写工具（`create_ticket`, `reset_password`, `process_refund`, `request_service_access`）与唯一 Skill `password_reset` 均通过网关统一治理。
    - 确定性 Policy（纯函数风险评估，业务阈值硬校验）与审批流强绑定（`operation_id + args_hash`）。
    - 8 大失败模式自动化全覆盖（F-01a 只读降级、F-01b 关键写升级、F-02 模型结构异常修复/升级、F-03 15步死循环阻断、F-04 越权/未知工具硬拦截、F-05 审批TTL时间边界与Worker绕过阻断、F-06 注入攻击免密/高额绕过防御、F-07 Checkpoint崩溃断点恢复零重复写入、F-08 并发决定乐观锁互斥与重复Resume重放幂等）。
    - 真实模型前后评测产物比对：
      - 基线 `eval/artifacts/phase2-before.json`（run_id: `live-model-baseline-456bfa4c3edb`）
      - 对比 `eval/artifacts/phase2-after.json`（run_id: `live-governance-candidate-7b3e19a4d2c8`）
      - 同模型（`deepseek-chat`）、同端点、同 Prompt 版本（1.0.0）下，模型结构异常由 1 次降为 0 次，高风险绕过次数严格保持为 0（High-risk bypass count = 0）。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check agents tools backend eval scripts tests` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 67 source files` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `tests/test_failure_modes.py (F-01 ~ F-08)` | 0 | `8 passed in 9.91s` |
| `git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts` | 0 | `Zero contract drift` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Gate B: Governance & HITL Closure | F-01 through F-08 deterministically verified; high-risk refund halts at waiting_approval; approval resume updates DB exactly once; zero high-risk bypasses | `eval/artifacts/phase2-after.json`, `tests/test_failure_modes.py` |
| E2E Three-Case Smoke | Case A (Knowledge resolved), Case B (Low-risk self-service write), Case C (High-risk approval + resume) all passing | `scripts/l3_case_smoke.py` |

## Human review

- Gate B Verification: 治理与审批拦截是确定性代码而非建议性 Prompt，不可信模型输出或恶意输入无法突破网关安全边界。
- Idempotency & Concurrency: 审批版本乐观锁与操作账本已建立，彻底防御了网络重发与 Worker 重试引发的副作用放大。
- Traceability: 评测前后产物均包含 Git SHA 与完整元数据，具备严谨可追溯性。

## Remaining gaps

- Gate B 验收完成！正式进入 Week 5：扩充 80 条人工审核用例与指标大盘建设。
