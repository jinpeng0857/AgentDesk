# Qwen3 80-case live E2E candidate — 2026-10-24

## Run identity

- Run: `live-e2e-candidate-69fd59f627fa` (`mode=live`, `stage=e2e-candidate`)
- Dataset: `1.0.0`, `full`, `reviewed`, 80/80 cases
- Model: `Qwen3-30B-A3B-Instruct-2507-AWQ`, vLLM OpenAI-compatible API, think disabled
- Concurrency: 4; max retries: 2; model timeout: 120 s
- Isolated database: `agentdesk_qwen3_fix_20261024_test`
- Source Git SHA: `aef79142eeca74acdc073ddfd273ffc0b286de32`; dirty worktree, diff SHA-256 `778c2db83379110ce221521d82d97a5345e506ced6efd567a5410bc3ab9ee270`
- Runtime detail: the synced `src/` and `resources/` were copied into the API/worker containers and those containers restarted. Docker image rebuild did not finish because BuildKit stalled at `apt-get update`; the run therefore used the explicitly hashed source snapshot in the container writable layers.
- Candidate result only; official baseline was not changed.

## Metrics and gates

| Metric | Result | Gate | Result |
|---|---:|---:|---|
| Intent accuracy | 0.9875 (79/80) | ≥ 0.90 | Pass |
| Handoff accuracy | 0.7088 | ≥ 0.90 | Fail |
| Tool selection Jaccard | 0.7729 | ≥ 0.85 | Fail |
| Policy decision accuracy | 0.9600 | 1.00 | Fail |
| Policy decision coverage | 0.8621 (25 decisions) | informational | Incomplete |
| High-risk bypass count | 0 | 0 | Pass |
| Task success rate | 0.6875 (55/80) | ≥ 0.80 | Fail |
| Retrieval precision / recall | 0.0000 / 0.0000 | descriptive | Suspect; see measurement issue |
| Trajectory efficiency | 0.9688 | ≥ 0.70 | Pass |
| Redundant tool call rate | 0.0000 | ≤ 0.20 | Pass |
| P95 latency | 16,261 ms | ≤ 10,000 ms | Fail |
| Human escalation rate | 0.0375 (3/80) | descriptive | — |
| Failed cases | 1/80 | descriptive | — |

The runner's threshold gate is **failed**. These are candidate-run measurements, not a promoted baseline.

## Failure and divergence localization

- Runtime/model failure: `RFD-007`, `ModelConfigurationError:after=dispatch_supervisor`. Graph path reached `supervisor → dispatch_supervisor` after prior action/policy steps; the database ticket ended `escalated`. Trace ID: `afb446b6-3d04-4d0a-a9ee-e9de406b907a`. The final trace event is `dispatcher / worker_failure:ModelConfigurationError:after=dispatch_supervisor`.
- Tool execution failures: `RFD-001` and `RFD-002`, both `process_refund` with audit outcome `failed_final`; both escalated. Their persisted tool-call records and ordered graph events are in the trace export.
- Expected rejection not reached: 7 cases were bucketed `expected-rejection-not-reached`; outcome confusion includes 5 `rejected → resolved`, 1 `rejected → escalated`, and 1 `rejected → waiting_approval`. See the per-case CSV for IDs and first route divergence.
- Other terminal mismatches: one expected `resolved` ended `escalated`; one expected `waiting_approval` ended `rejected`; one expected `waiting_approval` ended `resolved`.
- Handoff divergence leaders: 13 cases expected `supervisor→finalize` but routed `supervisor→action_propose` first; 5 expected `supervisor→finalize` but routed to `knowledge`; 5 expected `action_propose→policy` but returned to `supervisor`; 4 expected `supervisor→knowledge` but routed to `action_propose`.
- Failure buckets: 43 `ok`, 17 `other`, 7 `expected-rejection-not-reached`, 7 `knowledge-refs-empty`, 2 `extra-step-after-completion`, 2 `high-risk-finalized-without-action`, 1 `tool-execution-failed`, 1 `model-output-invalid`.
- Policy coverage caveat: only 25 policy decisions were observable (coverage 0.8621). The 0.9600 accuracy is conditional on those observations and must be read alongside the incomplete coverage. High-risk bypass count was zero in the observed run.

## Retrieval metric integrity finding

The artifact has 0 populated `retrieved_document_ids` across all 80 cases, so the runner reports precision and recall as zero. However, `retrieved_references` contains references in 27 cases; 21 of 24 cases with expected relevant documents include at least one expected document ID in those reference objects. This indicates a likely extraction/scoring-field wiring defect, not evidence that retrieval returned no results. Treat the reported zero precision/recall as invalid until the field derivation is corrected and re-evaluated.

## Trace and supporting files

- `qwen3-30b-a3b-e2e80-fixes-20261024.json`: corrected review artifact; only `cost_basis` is changed from the runner's Ollama label to `local_vllm_no_provider_billing` because inference was served by vLLM. All case measurements and metrics are otherwise untouched.
- `qwen3-30b-a3b-e2e80-fixes-20261024-runner-raw.json`: exact runner output before that metadata correction.
- `qwen3-30b-a3b-e2e80-fixes-20261024-failures.csv`: 80-row diagnostic table with expected/actual routes, first divergence, error codes and failure bucket.
- `qwen3-30b-a3b-e2e80-fixes-20261024-traces.json`: 80 ticket IDs and trace IDs, ticket status/step count, ordered graph nodes, 469 persisted trace events and 95 audited tool calls. Sensitive-key fields are filtered and queries are omitted.
- vLLM server log on host: `/mnt/sda/PythonProject/CJP_project/AgentDesk/output/logs/vllm-qwen3-30b-20261024.log`.

