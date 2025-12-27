# CI Gates

## CI entrypoint

The CI entrypoint is:

```
./scripts/ci_gates.sh
```

This script discovers the canonical gate runner, executes it, and emits auditable artifacts under `artifacts/`.

## Artifacts

CI always writes and uploads the following files under `artifacts/`:

- `artifacts/gates.log` (full gate output)
- `artifacts/proof_summary.json` (machine-readable summary)

If present, it also copies:

- `run_complete.json`
- any `*_latest.json` pointers

## Forced-fail demonstration (manual)

To trigger a controlled failure for evidence-pack validation, run the workflow manually:

1. Open the **CI Gates** workflow in GitHub Actions.
2. Select **Run workflow** and set `force_fail` to `true`.

This sets `CI_FORCE_FAIL=1` only for that manual run, causing the gates to fail after execution while still producing and uploading the evidence pack.
