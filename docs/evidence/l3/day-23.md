# Day 23 Acceptance

- Date (UTC+8): 2026-09-20
- Goal: 扩充至 80 条人工审核 Golden Cases 与数据集校验
- Changed files:
  - eval/golden_dataset.json
  - scripts/validate_golden_dataset.py
  - tests/unit/test_golden_dataset.py
  - docs/evidence/l3/day-23.md
- Contract/permission/state/eval impact:
  - 评测集全面升级为完整评估版本（Full Golden Dataset v1.0.0）：
    - 用例总数扩充至恰好 80 条，`level="full"`, `review_status="reviewed"`。
    - 5 大 Intent 均衡覆盖（各 16 条，16 * 5 = 80 条）：
      - `network`: 16 条（VPN、Wi-Fi、邮件服务、DNS、SSL、网口、丢包、DHCP、超时降级 F-01a 等）
      - `account`: 16 条（本人重置、越权他人重置拦截、锁定自愈、MFA、离职合规、临时凭据 Skill 等）
      - `permission`: 16 条（堡垒机只读/读写、K8s 集群、Jira、GitHub 组织、特权访问高风险审批等）
      - `query`: 16 条（订单状态、退款政策检索、物流承运商、多商品发票、资产归还、非法订单格式等）
      - `high_risk`: 16 条（大额退款审批 HITL、注入攻击越权调用拦截、F-01b 写超时熔断、F-04 越权拦截、F-05 TTL过期防御、F-06 哈希篡改防御、F-07 崩溃恢复、F-08 重放幂等、乐观锁并发等）
    - 完整覆盖生产全量 8 个 Tool（`check_service`, `create_ticket`, `process_refund`, `query_order`, `query_user`, `request_access`, `reset_password`, `search_knowledge`）。
    - 12 项必要字段（case_id, query, requester_id, expected_intent, expected_handoffs, expected_tools, expected_risk, expected_require_human, expected_outcome, relevant_document_ids, min_business_steps, reference_answer, tags）100% 具备且通过类型与逻辑校验。
    - 静态不变量强校验：
      - `expected_require_human=True` 强制约束 `expected_risk="high"`。
      - `expected_outcome="waiting_approval"` 强制约束 `expected_require_human=True`。
      - 涉及写操作的 expected_tools 强制路径包含 `policy`。
      - 存在 `relevant_document_ids` 强制 expected_tools 包含 `search_knowledge`。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run python scripts/validate_golden_dataset.py` | 0 | `case_count: 80, 16 per intent, 8 tools covered, version 1.0.0` |
| `uv run pytest tests/unit/test_golden_dataset.py -v` | 0 | `6 passed in 0.08s` |
| `uv run ruff check scripts/validate_golden_dataset.py tests/unit/test_golden_dataset.py` | 0 | `All checks passed!` |
| `uv run mypy scripts/validate_golden_dataset.py tests/unit/test_golden_dataset.py` | 0 | `Success: no issues found in 2 source files` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Golden Dataset Integrity | 80 reviewed cases loaded without error, schema verified, no unknown tools, zero unapproved write bypasses | `eval/golden_dataset.json`, `scripts/validate_golden_dataset.py` |

## Human review

- Dataset Scale & Rigor: 样本数突破 80 条门槛，脱离 smoke 阶段，达到具备统计信度的全量评估集水准。
- Invariant Consistency: 100% 遵守网关边界，写操作必须经由 Policy 节点。
- Label Quality: 所有标签均经过人工审核确认（`review_status=reviewed`），非未经校验的伪标签。

## Remaining gaps

- Day 23 目标圆满达成！进入 Day 24：实现全套确定性评估指标引擎与数值测试。
