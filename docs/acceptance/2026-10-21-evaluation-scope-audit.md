# 2026-10-21 Evaluation Scope Audit

This report separates API admission, Worker execution, and full business-flow evidence.
The 50-case live-model run is a smoke run, not full acceptance; full E2E acceptance
requires all 80 reviewed golden cases.

## Evidence and interpretation

| Measurement | Evidence | Result and boundary |
|---|---|---|
| FastAPI submission admission | `artifacts/evaluation/candidates/fastapi-50-concurrent-20261021.local.json` | Three rounds, each with 50 concurrent authenticated POSTs and 50 HTTP 202 responses. Per-round admission throughput: 37.28, 49.39, and 44.70 requests/s; nearest-rank HTTP response P95: 1325.74, 970.37, and 1071.82 ms. Worker was off. These are API admission measurements, not Worker throughput or full-ticket completion. |
| 50-case real-model E2E smoke | `artifacts/evaluation/candidates/api-arq-live-model-e2e-smoke-50-rerun-20261021.local.json` | 50 unique tickets; 9 ARQ jobs returned successfully and 41 failed. Persisted statuses: 9 escalated, 41 running, 0 resolved. Thus business-resolved rate is 0/50. Only `search_knowledge` ran (55 calls); approval records and write operations were both zero. This run does not establish the complete Policy/Tool-write/Approval path. |
| Worker queue/execution | Same 50-case E2E artifact | `worker_max_jobs=1`: jobs were submitted as a batch but executed serially by one Worker slot. Observed batch throughput was 0.023 tickets/s. Ticket-created-to-job-finished P95 was 2,044,075.68 ms (about 34.1 minutes) and includes queue wait; it is not an API response P95. |
| E2E submit HTTP response P95 | Same 50-case E2E artifact | `null` / not measured. The original monitor stopped after 44 requests because of a field-access error. Six additional tickets were submitted and DB uniqueness confirmed 50 total, but the missing per-request timings cannot be reconstructed from ticket rows. Do not substitute the separate API-admission benchmark above. |
| Trace debugging-time reduction | Same artifact | Not measured (`null`): no controlled before/after investigator protocol or timed sample. |
| Verified model-hallucination dead loops | Same artifact | Not measured (`null`); no qualifying loop trace was observed, which is not evidence that the rate is zero. |
| Full 80-case E2E acceptance | `src/eval/e2e.py` and `resources/evaluation/golden_dataset.json` | The runner refuses non-reviewed, non-`full`, or non-80-case datasets. A 50-case subset is smoke-only and cannot produce a full-acceptance result. |

## Process corrections

- Targeted unit/failure-mode tests, model-baseline runs, API admission benchmarks, and
  full-graph E2E are separate evidence layers. Passing one layer never implies another.
- The API benchmark runner writes each completed response to a flushed JSONL record
  before aggregation; it reports P95 only when every requested timing is retained.
  It labels itself admission-only and never reports Worker throughput.
- Classification latency P95 now uses nearest-rank P95 rather than the maximum sample.
- Full E2E dataset eligibility is directly unit-tested: only the unchanged reviewed
  80-case dataset is accepted.
- Historical missing HTTP timings remain unavailable. They are not imputed or
  backfilled from another benchmark.
