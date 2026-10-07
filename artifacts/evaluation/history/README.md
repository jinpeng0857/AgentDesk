# Historical evaluation artifacts

These files are retained as historical records only. They were produced by
earlier runner versions and are not current acceptance evidence or an official
quality baseline. The present staged runner is `uv run python -m eval.run_eval`.

| Artifact | What it actually records | Current use |
|---|---|---|
| `offline-fake-baseline.json` | Offline deterministic fake output | Harness/history only; not model quality |
| `historical-offline-fake-l3-final.json` | 80-case offline fake, despite its former “live” filename | Harness/history only |
| `phase1-before.json`, `phase2-before.json` | 20-case live classification measurements from their original runs | Historical classification comparison only |
| `phase1-after.json`, `phase2-after.json` | Legacy “E2E” files from a runner that filled graph/tool/outcome/latency metrics from expected labels | Invalid as E2E evidence; do not compare or cite their E2E metrics |

New full E2E runs must execute every reviewed case through the current graph,
real tool gateway, PostgreSQL and checkpoint saver. Results go to
`artifacts/evaluation/candidates/` and require human review; the runner never
promotes them to an official baseline.

## Harness command

```powershell
uv run python -m eval.run_eval --mode offline --stage model-baseline `
  --output artifacts/evaluation/candidates/offline-harness.local.json
```

This verifies the evaluation harness only. A local live full-graph candidate
requires Ollama, PostgreSQL and the isolated `_test` database:

```powershell
$env:EVAL_TEST_DATABASE_URL = "postgresql+asyncpg://agentdesk:agentdesk@127.0.0.1:5432/agentdesk_eval_test"
uv run python -m eval.run_eval --mode live --stage e2e-candidate `
  --model qwen3:8b --base-url http://localhost:11434/v1 `
  --database-url $env:EVAL_TEST_DATABASE_URL `
  --output artifacts/evaluation/candidates/qwen3-8b-e2e-candidate.json
```

Comparison requires the same reviewed dataset, stage, model and endpoint. Prompt
version differences are recorded and do not silently invalidate a comparison.
