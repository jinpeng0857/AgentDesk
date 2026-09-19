# Day 07 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: Phase 1 Smoke、CI 与 live 前后对比（Gate A 全量验收）
- Commit SHA: `PENDING`
- Changed files:
  - `scripts/phase1_smoke.py`
  - `.github/workflows/backend-ci.yml`
  - `.github/workflows/frontend-ci.yml`
  - `.github/workflows/eval.yml`
  - `eval/artifacts/phase1-after.json`
  - `README.md`
  - `docs/architecture.md`
  - `PORTFOLIO.md`
  - `docs/evidence/l3/day-07.md`
- Contract/permission/state/eval impact:
  - Phase 1 Definition of Done 12 项全部通过，Gate A 正式解除。
  - 交付 `scripts/phase1_smoke.py` 自动化端到端冒烟验证脚本：涵盖请求人登录、HTTP 202 入队、轮询直至 resolved、校验五字段知识库卡片、无密钥泄露的按序 Trace、以及零业务写工具调用的严格审计。
  - 交付三个标准化 GitHub Actions 工作流：`backend-ci.yml`（Postgres/Redis 容器化服务、迁移、Lint、TypeCheck、契约漂移与单元/集成测试）、`frontend-ci.yml`（Lint、OpenAPI 类型对齐、TypeCheck、Vitest 与构建）、`eval.yml`（PR 运行离线评测 Harness，手动受保护触发 Live 评测）。
  - 在干净工作区产出 `eval/artifacts/phase1-after.json`，与 Day 01 冻结的 `phase1-before.json` 严格比对元数据（相同模型 `deepseek-chat`、相同端点、相同 Prompt 版本 `1.0.0`），意图分类匹配度 17/20 保持稳定。
  - 主文档（`README.md`、`docs/architecture.md`、`PORTFOLIO.md`）同步更新真实状态：宣布 Phase 1 完成，明确写操作治理与 HITL 仍属 Phase 2，杜绝任何简历通胀与不实百分比。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv lock --check` | 0 | `Resolved 146 packages in 2ms` |
| `uv run ruff check agents tools skills backend eval scripts tests` | 0 | `All checks passed!` |
| `uv run mypy agents tools backend eval scripts` | 0 | `Success: no issues found in 58 source files` |
| `uv run pytest tests/unit -q` | 0 | `141 passed in 2.80s` |
| `uv run pytest tests/integration -q` | 0 | `54 passed in 14.02s` |
| `uv run python scripts/export_tool_schemas.py --check` | 0 | `Tool schema snapshot is current` |
| `uv run python scripts/export_openapi.py --check` | 0 | `OpenAPI snapshot is current` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run python scripts/validate_golden_dataset.py` | 0 | `20 cases valid across 5 intents and 8 tools` |
| `npm run lint` (frontend) | 0 | `eslint . --max-warnings=0 (passed)` |
| `npm run type-check` (frontend) | 0 | `vue-tsc --noEmit (passed)` |
| `npm test` (frontend) | 0 | `5 passed in 1.05s` |
| `npm run build` (frontend) | 0 | `built in 115ms (dist/index.html, assets)` |
| `git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts` | 0 | `Zero contract drift` |
| `uv run python eval/run_eval.py --mode live --stage e2e-candidate --compare eval/artifacts/phase1-before.json --output eval/artifacts/phase1-after.json` | 0 | `cases=20 intent_match_count=17 failures={}` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Live Candidate Comparison | Evaluated with deepseek-chat against phase1-before.json baseline: matched_dataset_version=True, matched_model=True, matched_base_url_id=True, matched_prompt_versions=True | `eval/artifacts/phase1-after.json` |
| Automated Smoke & Cross-Restart Persistence | Login -> Submit -> 202 -> Polling -> Resolved -> 5 citation fields -> Redacted Trace -> 0 write tool calls | `scripts/phase1_smoke.py`, `tests/integration/test_worker_recovery.py` |
| Zero Side-Effects Guarantee | Zero write adapter calls; read executor handles only read operations | `backend/governance/executor.py`, `tests/integration/test_graph_read_only.py` |

## Human review

- Authorization boundary: 确认 Supervisor 绝不调用 Tool，API 绝不执行 Graph，写操作在 Phase 1 保持零副作用且全部通过网关确定性拒绝并审计。
- Sensitive-data review: 确认 `phase1-after.json`、`day-07.md` 及前端所有产物均未泄露 API 密钥、数据库口令或未脱敏凭据。
- Test-quality review: 单元测试（141 个）与集成测试（54 个）全量通过；前端组件与 Pinia 状态测试全部通过；live 评测与 baseline 对比元数据严丝合缝。
- Documentation accuracy: README、架构方案与 PORTFOLIO 均保持严谨克制，明确指出 Phase 1 完成仅代表只读纵切跑通，Phase 2 写操作与审批流程尚未开始。

## Remaining gaps

- Phase 1 完全闭环。正式进入 Phase 2（Day 08：数据模型与空库迁移）。
