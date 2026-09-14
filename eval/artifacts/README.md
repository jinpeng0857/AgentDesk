# Evaluation artifacts

This directory holds committed, human-reviewed evaluation artifacts. Each file is
produced by `eval/run_eval.py` and records the metadata required to trace it back
to the code and configuration that produced it.

## What an artifact contains

- `run_id`, `mode` (`offline` or `live`), `stage`
- `dataset_version`, `dataset_level`, `dataset_review_status`
- `model`, `base_url_id` (endpoint host only — never a full URL)
- `prompt_versions` for all four agents
- `git_sha` and `worktree_clean`
- `created_at` in UTC
- per-case structured output and `failure_code`
- raw counts: `intent_match_count`, `failure_counts`
- `interpretation`, stating what the artifact does and does not prove

## Rules

1. **No percentages.** Counts are raw observations. Design thresholds live in
   `AGENTS.md` §7.2 and must never be restated as measured results.
2. **`eval/baseline.json` is never written here.** Only an explicit
   `--update-baseline` flow may produce a candidate baseline, and it requires
   human review before it is committed.
3. **Live artifacts require a clean worktree.** The runner refuses to start a
   live baseline when `git status --porcelain` is non-empty, so the recorded
   `git_sha` always identifies the evaluated code.
4. **Offline artifacts are harness self-tests.** A deterministic fake model
   proves prompt loading, the structured-output boundary, metadata capture and
   failure classification. It proves nothing about prompt or model quality, and
   every offline artifact says so in `interpretation`.

## Commands

```powershell
uv run python eval/run_eval.py --mode offline --stage model-baseline `
  --output eval/artifacts/phase1-offline-smoke.local.json

uv run python eval/run_eval.py --mode live --stage model-baseline `
  --output eval/artifacts/phase1-before.json
```

Offline smoke output uses the `*.local.json` suffix, which `.gitignore` excludes:
a self-test artifact must never be mistaken for a committed live baseline.

`--compare <previous-artifact>` refuses to compare artifacts whose dataset
version, model, endpoint host or prompt versions differ.
