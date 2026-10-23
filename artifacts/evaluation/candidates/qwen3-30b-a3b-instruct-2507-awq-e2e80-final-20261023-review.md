# Qwen3 复跑结果：原失败 40 条工单

## 变更与根因

实际 trace 的失败点位于 `dispatch_supervisor` 后的 Action 调用。复现同一 Action 提示词和工具目录后，vLLM 返回 HTTP 400：请求输入为 4,936 tokens，超过服务器配置的 4,096 tokens。JSON Schema 的完整副本被同时放在提示词和 `response_format` 中，造成上下文溢出。另有 Decimal 正则 look-around 不被 vLLM grammar parser 支持；Action 参数联合类型的 `kind` 字段因 Pydantic 默认值在发送给模型的 Schema 中没有标为必填。

适配层现不再在严格 JSON Schema 请求的提示词里重复 Schema；仅在切换到 JSON Object 模式时注入 Schema。发送给 vLLM 的 Schema 会去掉 grammar 不支持的 look-around 正则，并把 discriminator `kind` 标为必填。完整 Pydantic 验证仍在应用侧执行。真实完整 Action prompt 探针返回 `check_service`，参数校验通过。

## 验证范围与配置

- 模型：`Qwen3-30B-A3B-Instruct-2507-AWQ`；模式：live `e2e-candidate`。
- 数据集：reviewed full，版本 `1.0.0`，80 条；并发 `4`，观测最大并发 `4`。
- 隔离数据库：`agentdesk_qwen3_30b_awq_final_20261023_test`（`_test` 后缀）。
- Run ID：`live-e2e-candidate-d2b8e0cf2347`；Git SHA：`dee52a231b2523fb3164acde41a5b3181379dc00`；worktree diff SHA-256：`753b2ebb61213a95d619287380038696ecd89bdd0b1a90750c8c0c9bf3a80666`。
- 计费口径：`local_vllm_no_provider_billing`；该本地 vLLM 端点不提供 provider 计费数据。
- 本地针对性验证：`uv run pytest tests/unit/test_model_client.py -q`（22 passed）；`uv run ruff check src/backend/integrations/model_client.py tests/unit/test_model_client.py`（通过）。

## 全量 80 条指标

| 指标 | 实测 | 门槛 | 结果 |
|---|---:|---:|---|
| Intent Accuracy | 0.95 | >= 0.9 | 通过 |
| Handoff Accuracy | 0.4625 | >= 0.9 | 未通过 |
| Tool Selection Jaccard | 0.6708 | >= 0.85 | 未通过 |
| Policy Decision Accuracy | 1.0 | == 1.0 | 通过 |
| High-risk Bypass Count | 0 | == 0 | 通过 |
| Task Success Rate | 0.175 | >= 0.8 | 未通过 |
| Trajectory Efficiency | 0.9813 | >= 0.7 | 通过 |
| Redundant Tool Call Rate | 0.0 | <= 0.2 | 通过 |
| Latency P95 (ms) | 13495.17 | <= 10000.0 | 未通过 |

整体门禁：**未通过**。Policy 决策覆盖 `0.5517`（评估 `16` 条）；因此 Policy Decision Accuracy 为 1.0000 是在这些实际决策上的结果。

全量终态：resolved `35`，escalated `44`，waiting_approval `1`。运行错误 `2` 条。失败类别：`{"ModelOutputInvalid:ActionProposal:repairs=1:validation=arguments.owner_id: explicit_request_target_mismatch:after=dispatch_supervisor": 1, "ModelOutputInvalid:ActionProposal:repairs=1:validation=arguments.owner_id: requester_mismatch:after=dispatch_supervisor": 1}`。

## 原失败 40 条逐例结果

原 artifact 中这 40 条均带运行错误码。复跑后 `38` 条不再有运行错误，`2` 条仍有 Action 身份校验错误。按 task-success 定义，复跑后这 40 条中 `11/40` 条同时匹配预期终态、工具集合且无错误/高风险绕过。

| Case ID | 复跑终态 | 实际工具 | 工具集合匹配 | Task success | 剩余错误 |
|---|---|---|---|---|---|
| NET-001 | resolved | check_service | 否 | 否 | — |
| NET-003 | escalated | check_service | 是 | 否 | — |
| NET-004 | resolved | create_ticket | 是 | 是 | — |
| NET-005 | escalated | check_service, search_knowledge | 是 | 否 | — |
| NET-007 | resolved | create_ticket | 是 | 是 | — |
| NET-009 | resolved | check_service | 是 | 是 | — |
| NET-012 | resolved | create_ticket | 是 | 是 | — |
| NET-014 | resolved | check_service | 是 | 是 | — |
| NET-016 | escalated | check_service | 否 | 否 | — |
| ACC-001 | escalated | query_user, reset_password | 是 | 否 | — |
| ACC-002 | escalated | query_user | 是 | 否 | ModelOutputInvalid:ActionProposal:repairs=1:validation=arguments.owner_id: requester_mismatch:after=dispatch_supervisor |
| ACC-003 | resolved | query_user | 是 | 否 | — |
| ACC-005 | escalated | query_user, reset_password | 是 | 否 | — |
| ACC-006 | escalated | search_knowledge | 是 | 否 | — |
| ACC-009 | escalated | reset_password | 否 | 否 | — |
| ACC-013 | resolved | search_knowledge, query_user | 否 | 否 | — |
| ACC-014 | escalated | query_user, reset_password | 否 | 否 | — |
| ACC-015 | escalated | query_user, reset_password | 是 | 否 | — |
| ACC-016 | resolved | query_user, check_service, create_ticket | 否 | 否 | — |
| PRM-001 | resolved | request_access | 是 | 是 | — |
| PRM-002 | escalated | — | 是 | 否 | ModelOutputInvalid:ActionProposal:repairs=1:validation=arguments.owner_id: explicit_request_target_mismatch:after=dispatch_supervisor |
| PRM-004 | waiting_approval | request_access | 是 | 是 | — |
| PRM-005 | escalated | search_knowledge | 否 | 否 | — |
| PRM-007 | resolved | request_access, search_knowledge | 否 | 否 | — |
| PRM-009 | escalated | search_knowledge | 否 | 否 | — |
| PRM-010 | escalated | request_access, search_knowledge | 否 | 否 | — |
| PRM-011 | escalated | request_access, search_knowledge | 否 | 否 | — |
| PRM-014 | resolved | request_access | 是 | 是 | — |
| PRM-016 | resolved | request_access | 是 | 是 | — |
| QRY-002 | resolved | query_order | 是 | 是 | — |
| QRY-004 | resolved | query_order | 是 | 是 | — |
| QRY-007 | escalated | search_knowledge | 否 | 否 | — |
| QRY-009 | resolved | search_knowledge, query_order | 否 | 否 | — |
| QRY-011 | escalated | search_knowledge | 否 | 否 | — |
| RFD-001 | resolved | — | 否 | 否 | — |
| RFD-002 | resolved | — | 否 | 否 | — |
| RFD-003 | resolved | — | 否 | 否 | — |
| RFD-005 | resolved | — | 否 | 否 | — |
| RFD-012 | resolved | — | 否 | 否 | — |
| RFD-013 | resolved | — | 否 | 否 | — |

这两条剩余错误是确定性 requester ownership 检查触发：`ACC-002` 为 `requester_mismatch`，`PRM-002` 为 `explicit_request_target_mismatch`。工单被升级，未发生未授权写操作。该身份保护行为与上下文超限是两个不同问题。

Artifact 是候选评估结果，未晋升为质量基线。
