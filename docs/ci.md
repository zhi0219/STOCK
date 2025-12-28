# CI Gates

## CI entrypoint

The CI entrypoint is:

```
./scripts/ci_gates.sh
```

This script discovers the canonical gate runner, executes it, and emits auditable artifacts under `artifacts/`.

## Canonical invocation

Gate runners are invoked in module mode to ensure `import tools...` resolves consistently:

```
python -m tools.<gate_runner>
```

Local path-mode invocations remain supported on a best-effort basis:

```
python tools/<script>.py
```

Module mode avoids `ModuleNotFoundError` when CI executes tools directly.

## Job Summary

When running in GitHub Actions, the script also appends the job summary to the **Summary** tab via `GITHUB_STEP_SUMMARY`. The same content is always written to `artifacts/ci_job_summary.md`.

## Log truncation

Logs are capped by `CI_MAX_LOG_KB` (default: `2048`). If `artifacts/gates.log` exceeds the limit, it is truncated to include the head and tail with a `===LOG_TRUNCATED===` marker in between. The proof summary records `log_truncated`, `log_bytes_original`, `log_bytes_final`, and `max_log_bytes`.

## Artifacts

CI always writes and uploads the following files under `artifacts/`:

- `artifacts/gates.log` (gate output, bounded by truncation)
- `artifacts/proof_summary.json` (machine-readable summary)
- `artifacts/action_center_report.json` (Action Center report)
- `artifacts/ci_job_summary.md` (human-readable CI summary)

If present, it also copies:

- `run_complete.json`
- any `*_latest.json` pointers

## Manual demo inputs (workflow_dispatch)

The **CI Gates** workflow supports optional inputs for safe demonstrations:

- `max_log_kb`: override the log cap in KB for that run.
- `log_spam`: emit harmless `CI_LOG_SPAM_DEMO` filler lines before gates to help exercise truncation.

## Forced-fail demonstration (manual)

To trigger a controlled failure for evidence-pack validation, run the workflow manually:

1. Open the **CI Gates** workflow in GitHub Actions.
2. Select **Run workflow** and set `force_fail` to `true`.

This sets `CI_FORCE_FAIL=1` only for that manual run, causing the gates to fail after execution while still producing and uploading the evidence pack.
