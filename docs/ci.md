# CI Gates

## CI entrypoint

The CI entrypoint is:

```
./scripts/ci_gates.sh
```

This script discovers the canonical gate runner, executes it, and emits auditable artifacts under `artifacts/`.
For PR40, the canonical gate runner is `tools/verify_pr40_gate.py`. The preflight runner remains
`tools/verify_pr36_gate.py`. CI also runs `tools.compile_check` (targets: `tools`, `scripts`),
`tools.syntax_guard`, `tools.ps_parse_guard`, and `tools.ui_preflight` early to fail closed on
Windows UI launch regressions.

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
- `artifacts/trade_activity_report.json` (PR38 trade activity audit)
- `artifacts/ci_job_summary.md` (human-readable CI summary)
- `artifacts/repo_hygiene.json` (repo hygiene scan output)
- `artifacts/compile_check.log` (compile check log)
- `artifacts/compile_check_result.json` (compile check result)
- `artifacts/syntax_guard_result.json` (syntax guard results)
- `artifacts/syntax_guard_excerpt.txt` (syntax guard excerpt)
- `artifacts/ps_parse_result.json` (PowerShell parse gate result)
- `artifacts/ui_preflight_result.json` (UI preflight gate result)
- `artifacts/walk_forward_result.json` (PR32 walk-forward summary)
- `artifacts/walk_forward_windows.jsonl` (PR32 walk-forward windows)
- `artifacts/no_lookahead_audit.json` (PR32 no-lookahead audit)
- `artifacts/Logs/train_runs/_pr33_gate/_latest/replay_index_latest.json` (PR33 replay index)
- `artifacts/Logs/train_runs/_pr33_gate/_latest/decision_cards_latest.jsonl` (PR33 decision cards)
- `artifacts/retention_report.json` (retention report)
- `artifacts/retention_prune_plan.json` (retention prune plan)
- `artifacts/retention_prune_result.json` (retention prune result)
- `artifacts/Logs/train_runs/recent_runs_index.json` (recent runs index)
- `artifacts/Logs/train_runs/_pr35_gate/stress_report.json` (PR35 stress report)
- `artifacts/Logs/train_runs/_pr35_gate/stress_scenarios.jsonl` (PR35 stress scenarios)
- `artifacts/overtrading_calibration.json` (PR39 calibration)
- `artifacts/regime_report.json` (PR39 regime classifier)

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

This sets `CI_FORCE_FAIL=1` only for that manual run, causing the gates to fail after execution while still producing and uploading the evidence pack. PR31 adds `PR31_FORCE_FAIL=1` for local evidence-pack validation. PR32 adds `PR32_FORCE_FAIL=1` to fail after walk-forward/no-lookahead artifacts are emitted. PR33 adds `PR33_FORCE_FAIL=1` to fail after replay artifacts are emitted. PR34 adds `PR34_FORCE_FAIL=1` to fail after retention artifacts are emitted. PR35 adds `PR35_FORCE_FAIL=1` to fail after stress artifacts are emitted. PR36 adds `PR36_FORCE_FAIL=1` to fail after compile-check artifacts are emitted. PR38 adds `PR38_FORCE_FAIL=1` to fail after syntax-guard/compile-check artifacts are emitted. PR39 adds `PR39_FORCE_FAIL=1` to force the compile check to fail via a temporary bad file.

## PR38 gate (local)

Run the PR38 gate locally using module mode:

```
python -m tools.verify_pr38_gate
```

To confirm fail-closed behavior while still emitting artifacts:

```
PR38_FORCE_FAIL=1 ./scripts/ci_gates.sh
```

## PR39 gate (local)

Run the PR39 gate locally using module mode:

```
python -m tools.verify_pr39_gate
```

To confirm fail-closed behavior while still emitting artifacts:

```
PR39_FORCE_FAIL=1 ./scripts/ci_gates.sh
```

To run the syntax guard directly:

```
python -m tools.syntax_guard
```

## PR40 gate (local)

Run the PR40 gate locally using module mode:

```
python -m tools.verify_pr40_gate
```

## PR36 gate (local)

Run the PR36 gate locally using module mode:

```
python -m tools.verify_pr36_gate
```

To confirm fail-closed behavior while still emitting artifacts:

```
PR36_FORCE_FAIL=1 ./scripts/ci_gates.sh
```

## PR32 gate (local)

Run the PR32 gate locally using module mode:

```
python -m tools.verify_pr32_gate
```
