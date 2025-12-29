# Walk-forward evaluation

## Overview

Walk-forward evaluation produces deterministic, SIM-only evidence showing how a candidate performs across sequential windows. The audit stays read-only and writes runtime outputs under `Logs/runtime/` unless an explicit output directory is provided.

## Outputs

Default outputs (runtime-only, ignored by git):

- `Logs/runtime/walk_forward/walk_forward_result.json`
- `Logs/runtime/walk_forward/walk_forward_windows.jsonl`
- `Logs/runtime/walk_forward/_latest/walk_forward_result_latest.json`
- `Logs/runtime/walk_forward/_latest/walk_forward_windows_latest.jsonl`

The no-lookahead audit writes:

- `Logs/runtime/no_lookahead/no_lookahead_audit.json`
- `Logs/runtime/no_lookahead/_latest/no_lookahead_audit_latest.json`

CI copies the artifacts into `artifacts/` for evidence packs.

## Run locally (module mode)

Walk-forward evaluation:

```
python -m tools.walk_forward_eval
```

No-lookahead audit:

```
python -m tools.no_lookahead_audit
```

## Custom output directory

To emit artifacts into the CI evidence pack directory:

```
python -m tools.walk_forward_eval --output-dir artifacts --latest-dir artifacts
python -m tools.no_lookahead_audit --output-dir artifacts --latest-dir artifacts
```

These commands stay SIM-only and do not place trades.
