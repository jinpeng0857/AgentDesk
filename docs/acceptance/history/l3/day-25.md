# Day 25 Acceptance

- Date (UTC+8): 2026-09-20
- Goal: Offline/Live Eval、baseline 与 artifact 持久化及 API 暴露
- Changed files:
  - alembic/versions/20260920_0003_eval_runs.py
  - backend/api/eval.py
  - backend/db/models.py
  - backend/db/repositories/eval_runs.py
  - eval/baseline.json
  - eval/run_eval.py
  - specs/openapi/openapi.json
  - frontend/src/types/api.d.ts
  - tests/unit/test_eval_api.py
  - tests/unit/test_eval_runner.py
  - tests/unit/test_openapi_contract.py
  - docs/evidence/l3/day-25.md
- Contract/permission/state/eval impact:
  - `eval_runs` 数据库表与 Alembic 迁移脚本完成，支持 `run_id`、`mode`、`dataset_version`、`git_sha`、`model`、`prompt_versions`、`metrics`、`created_at`。
  - `EvalRunRepository` 仓储类实现 `create_run`、`list_runs`、`get_run`。
  - `eval/baseline.json` 官方基线工件固化 (`baseline-v1.0.0-frozen`)，包含完整的 80 个 Case 基线结果。
  - `eval/run_eval.py` 升级，集成全部确定性指标计算，支持 `--update-baseline` 显式生成候选基线；离线与在线运行均能计算并持久化指标。
  - `GET /api/eval/runs` 与 `GET /api/eval/runs/{run_id}` 落地真实持久化查询，移除 501 Stub，OpenAPI 契约与 `frontend/src/types/api.d.ts` 保持零漂移同步。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run pytest tests/unit/test_eval_api.py tests/unit/test_eval_runner.py tests/unit/test_openapi_contract.py -v` | 0 | `11 passed in 2.38s` |
| `uv run pytest tests/unit -q` | 0 | `213 passed in 4.10s` |
| `uv run ruff check agents tools skills backend eval tests` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval` | 0 | `Success: no issues found in 61 source files` |
| `uv run python scripts/export_openapi.py --check` | 0 | `OpenAPI snapshot is current` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Offline Eval Runner | 80/80 cases evaluated deterministically with metrics and threshold evaluation | `eval/run_eval.py`, `eval/baseline.json` |
| Eval API Endpoints | `GET /api/eval/runs` and `GET /api/eval/runs/{run_id}` return typed responses matching schema | `backend/api/eval.py`, `tests/unit/test_eval_api.py` |

## Human review

- Baseline Transparency: Baseline 严格绑定 `dataset_version: 1.0.0`，显式标注门槛与基线指标，不混淆阈值与实测值。
- OpenAPI Zero Drift: API 响应与 OpenAPI 规范及前端类型定义完全一致，无任何手动伪造 DTO。

## Remaining gaps

- Day 25 目标全部达成！准备进入 Day 26：前端 TraceViewer 页面开发。
