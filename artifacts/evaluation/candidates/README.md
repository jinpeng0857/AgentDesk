# Evaluation candidates

This directory contains review candidates, not an automatically promoted
official baseline. Keep the original JSON artifacts with their per-case
traces, run metadata, comparison and threshold results.

- `model-baseline` measures actual model classifications and their measured
  latency only. It does not measure graph execution, tool choice or task success.
- `e2e-candidate` requires the full reviewed 80-case dataset and executes each
  case through the live model, LangGraph, tool gateway, PostgreSQL and checkpoint
  saver. Expected labels are used only for post-run scoring.
- `.local.json` files are ignored by Git for workstation runs; remove `.local`
  only when intentionally preparing a reviewed artifact for version control.
- Prompt version changes are recorded during comparison. Dataset, stage, model
  and endpoint must match for a like-for-like comparison.
- `--update-baseline` writes another review copy here. It never edits a baseline
  or promotes a candidate.

The historical offline fake baseline is under `../history/` and is only suitable
for harness regression, not quality or performance comparison.
