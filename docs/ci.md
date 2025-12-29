# CI Gates

## CI entrypoint

The CI entrypoint is:

```
./scripts/ci_gates.sh
```

This script discovers the canonical gate runner, executes it, and emits auditable artifacts under `artifacts/`.
For PR35, the canonical runner is `tools/verify_pr35_gate.py`.

## Job Summary

When running in GitHub Actions, the script also appends the job summary to the **Summary** tab via `GITHUB_STEP_SUMMARY`. The same content is always written to `artifacts/ci_job_summary.md`.

## Log truncation

Logs are capped by `CI_MAX_LOG_KB` (default: `2048`). If `artifacts/gates.log` exceeds the limit, it is truncated to include the head and tail with a `===LOG_TRUNCATED===` marker in between. The proof summary records `log_truncated`, `log_bytes_original`, `log_bytes_final`, and `max_log_bytes`.

## Artifacts

CI always writes and uploads the following files under `artifacts/`:

- `artifacts/gates.log` (gate output, bounded by truncation)
- `artifacts/proof_summary.json` (machine-readable summary)
- `artifacts/action_center_report.json` (Action Center report)
- `artifacts/action_center_apply_result.json` (Action Center apply result)
- `artifacts/action_center_apply_plan.json` (Action Center apply plan)
- `artifacts/doctor_report.json` (Doctor report)
- `artifacts/xp_snapshot.json` (Truthful XP snapshot)
- `artifacts/ci_job_summary.md` (human-readable CI summary)
- `artifacts/repo_hygiene.json` (repo hygiene scan output)
- `artifacts/Logs/train_runs/_pr33_gate/_latest/replay_index_latest.json` (PR33 replay index)
- `artifacts/Logs/train_runs/_pr33_gate/_latest/decision_cards_latest.jsonl` (PR33 decision cards)
- `artifacts/retention_report.json` (retention report)
- `artifacts/retention_prune_plan.json` (retention prune plan)
- `artifacts/retention_prune_result.json` (retention prune result)
- `artifacts/Logs/train_runs/recent_runs_index.json` (recent runs index)
- `artifacts/Logs/train_runs/_pr35_gate/stress_report.json` (PR35 stress report)
- `artifacts/Logs/train_runs/_pr35_gate/stress_scenarios.jsonl` (PR35 stress scenarios)

If present, it also copies:

- `run_complete.json`
- any `*_latest.json` pointers

PR30 may emit additional Doctor/action-center artifacts such as `artifacts/doctor_runtime_write.json` and
`artifacts/abs_path_sanitize_hint.json`.

Step summary excerpts are written to `artifacts/ci_job_summary.md` and mirrored into the GitHub Actions **Summary** tab via `GITHUB_STEP_SUMMARY`.

## Manual demo inputs (workflow_dispatch)

The **CI Gates** workflow supports optional inputs for safe demonstrations:

- `max_log_kb`: override the log cap in KB for that run.
- `log_spam`: emit harmless `CI_LOG_SPAM_DEMO` filler lines before gates to help exercise truncation.

## Forced-fail demonstration (manual)

To trigger a controlled failure for evidence-pack validation, run the workflow manually:

1. Open the **CI Gates** workflow in GitHub Actions.
2. Select **Run workflow** and set `force_fail` to `true`.

This sets `CI_FORCE_FAIL=1` only for that manual run, causing the gates to fail after execution while still producing and uploading the evidence pack. The PR31 gate also supports `PR31_FORCE_FAIL=1` for local evidence-pack validation. PR33 adds `PR33_FORCE_FAIL=1` to fail after replay artifacts are emitted. PR34 adds `PR34_FORCE_FAIL=1` to fail after retention artifacts are emitted. PR35 adds `PR35_FORCE_FAIL=1` to fail after stress artifacts are emitted.

## PR35 gate (local)

Run the PR35 gate locally using module mode:

```
python -m tools.verify_pr35_gate
```

To confirm fail-closed behavior while still emitting artifacts:

```
PR35_FORCE_FAIL=1 ./scripts/ci_gates.sh
```
