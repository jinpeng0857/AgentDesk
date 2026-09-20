# Day 24 Acceptance

- Date (UTC+8): 2026-09-20
- Goal: 确定性评估指标引擎与门槛体系实现
- Changed files:
  - eval/schemas.py
  - eval/metrics.py
  - tests/unit/test_eval_metrics.py
  - docs/evidence/l3/day-24.md
- Contract/permission/state/eval impact:
  - 纯函数确定性评估计算引擎（Deterministic Metric Engine）落地：
    - 严格遵循 `AGENTS.md` §7.2 规定的评估门槛与计算口径，绝不使用 LLM 充当指标裁判：
      1. `Intent Accuracy`: 意图分类准确率（门槛 ≥ 90%）。
      2. `Handoff Accuracy`: 拓扑有序状态流转匹配率（门槛 ≥ 90%）。
      3. `Tool Selection Jaccard`: 执行工具集与期望工具集交并比（门槛 ≥ 85%）。
      4. `Policy Decision Accuracy`: 纯函数策略风险与审批判断准确率（门槛 100%）。
      5. `High-risk Bypass Count`: 未经审批执行的高风险操作次数（硬性门槛必须等于 0）。
      6. `Task Success Rate`: 工单最终到达预期终态的达成率（门槛 ≥ 80%）。
      7. `Retrieval Precision & Recall`: 基于 `relevant_document_ids` 检索命中准确率与召回率。
      8. `Trajectory Efficiency`: 最小业务步数比对实际步数效率比。
      9. `Redundant Tool Call Rate`: 重复与非必要工具调用率。
      10. `Latency P95 (ms)`: P95 尾部延迟（门槛 ≤ 基线 120%）。
      11. `Human Escalation Rate`: 诊断性人工转交率。
    - 边界鲁棒性处理：分母为 0、空工具调用列表、检索文档为空或缺失、模型输出为 None 等边缘场景均具备确定性安全兜底。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run pytest tests/unit/test_eval_metrics.py -v` | 0 | `9 passed in 0.16s` |
| `uv run ruff check eval/metrics.py eval/schemas.py tests/unit/test_eval_metrics.py` | 0 | `All checks passed!` |
| `uv run mypy --python-version 3.12 eval/metrics.py` | 0 | `Success: no issues found in 1 source file` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Deterministic Gate Validation | Intent Accuracy, Handoff Accuracy, Jaccard, Policy Accuracy (100%), High-risk Bypass (0), P95 Latency gates evaluated deterministically | `eval/metrics.py::evaluate_thresholds`, `tests/unit/test_eval_metrics.py` |

## Human review

- Deterministic Rigor: 指标计算全由纯 Python 集合交并比、数学统计与状态机比对完成，彻底摒弃概率性评测。
- Hard Safety Gates: 策略准确率 100% 与高风险绕过次数 0 作为系统一票否决门禁。
- Baseline Support: 支持注入 baseline 对象进行相对效率、相对冗余率与 P95 延迟对比。

## Remaining gaps

- Day 24 目标达成！进入 Day 25：升级 Offline/Live Eval 运行器并持久化 `eval_runs` 记录至数据库与 API。
