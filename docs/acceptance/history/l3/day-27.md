# Day 27 Acceptance

- Date (UTC+8): 2026-09-20
- Goal: Frontend EvalDashboard 页面与确定性质量门槛看板落地
- Changed files:
  - frontend/src/views/EvalDashboardView.vue
  - frontend/src/views/EvalDashboardView.test.ts
  - frontend/src/stores/eval.ts
  - frontend/src/router/index.ts
  - frontend/src/api/client.ts
  - frontend/src/api/types.ts
  - docs/evidence/l3/day-27.md
- Contract/permission/state/eval impact:
  - `frontend/src/api/types.ts` 与 `frontend/src/api/client.ts` 导出并实现 `listEvalRuns()` 和 `getEvalRun(runId)`，严格绑定后端 `GET /api/eval/runs` 与 `GET /api/eval/runs/{run_id}` OpenAPI 契约。
  - `useEvalStore` 实现 `fetchRuns()` 与 `fetchRun(runId)`。
  - `EvalDashboardView.vue` 落地：
    - 左侧面板：运行历史列表（展示 `run_id`、`mode`、`dataset_version`、`created_at`、Git SHA 缩略）。
    - 选定运行元数据：展示目标模型 `model`、40 位 Hex Git SHA、评测集版本、各 Agent 的 Prompt 版本字典。
    - 核心看点：**门槛 (Design Threshold) 与 本次结果 (Actual Observed) 绝对严格区分**：
      - 明确展示设计门槛标签（如 `门槛: ≥ 90%`、`门槛: 100%`、`门槛: = 0`），禁止将门槛伪造成实测成绩。
      - 结构化显示 Intent Accuracy、Handoff Accuracy、Tool Selection Jaccard、Policy Decision Accuracy、High-risk Bypass Count（硬门禁 0 绕过）、Task Success Rate、P95 延迟等指标及 PASS / FAIL 状态徽章。
    - 科学严谨性声明：显式说明所有指标均由纯函数确定性计算，离线冒烟使用 Fake 模型仅作链路校验，不可解释为模型质量，彻底摒弃 LLM 主观裁判。
    - 路由集成：在 `frontend/src/router/index.ts` 中注册 `/eval` 路由。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `npm run type-check` (in frontend) | 0 | `vue-tsc --noEmit -p tsconfig.json` (0 errors) |
| `npm run test` (in frontend) | 0 | `14 passed in 1.62s` (5 test suites) |
| `npm run lint` (in frontend) | 0 | `eslint . --max-warnings=0` (0 warnings/errors) |
| `npm run build` (in frontend) | 0 | `vite build` completed in 168ms |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Eval Dashboard Quality Gates | Explicit threshold vs actual value rendering, gate passing assertions, metadata display | `frontend/src/views/EvalDashboardView.vue`, `frontend/src/views/EvalDashboardView.test.ts` |
| Navigation Route | Direct access to `/eval` | `frontend/src/router/index.ts` |

## Human review

- Threshold Honesty: 严格遵循 `AGENTS.md` §20 与 §7.2，在界面上彻底厘清“门槛”与“实测结果”，严防履历与演示中的夸大或混淆。
- Engineering Discipline: 无任何手写 DTO，无 `any` 类型泄露，完全遵循 OpenAPI 代码生成驱动。

## Remaining gaps

- Day 27 目标全部达成！进入 Day 28：Trace Exporter 协议与可选 Langfuse 集成。
