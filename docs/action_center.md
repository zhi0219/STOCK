# Action Center (SIM-only, READ_ONLY)

## What it does

The Action Center provides a deterministic, auditable loop to diagnose missing or stale artifacts, propose safe recovery actions, and (optionally) execute those actions with typed confirmation. It is **SIM-only** and **READ_ONLY** with respect to external accounts; it never places trades, connects to brokers, or modifies funds.

## Artifacts

The Action Center report is emitted as JSON with a stable schema:

- `Logs/action_center_report.json` (local UI)
- `artifacts/action_center_report.json` (CI evidence pack)

Related CI evidence artifacts:

- `artifacts/proof_summary.json`
- `artifacts/gates.log`

## Safety model

- Read-only by default (report generation only).
- Any action requires a typed confirmation token.
- Actions are blocked in CI environments.
- Fail-closed: action execution fails on any error and logs an audit event.
- Action Center apply requires `APPLY:<action_id>` and supports `--dry-run` for evidence-only runs.

## Apply (local CLI)

List safe actions:

```
.\.venv\Scripts\python.exe .\tools\action_center_apply.py
```

Dry-run an action (no mutations, evidence only):

```
.\.venv\Scripts\python.exe .\tools\action_center_apply.py --action-id ACTION_REBUILD_PROGRESS_INDEX --confirm APPLY:ACTION_REBUILD_PROGRESS_INDEX --dry-run
```

Apply an action with confirmation (SIM-only, local):

```
.\.venv\Scripts\python.exe .\tools\action_center_apply.py --action-id ACTION_REBUILD_PROGRESS_INDEX --confirm APPLY:ACTION_REBUILD_PROGRESS_INDEX
```

Evidence and events:

- Evidence pack defaults to `artifacts/action_center_apply/` with:
  - `action_center_apply_summary.json`
  - `action_center_apply.log`
- Apply attempts append to `Logs/events_YYYY-MM-DD.jsonl`.

## CI forced-fail demo

When `CI_FORCE_FAIL=1` is used for the workflow dispatch demo, the Action Center still writes `artifacts/action_center_report.json` and records a `CI_FORCE_FAIL` issue in the report so the evidence pack remains complete.

## UI location

The Tk UI exposes an **Action Center** panel under **Progress (SIM-only)** with:

- Latest report summary
- Buttons for the three safe recovery actions
- “Open latest evidence pack” convenience button (Windows only; other platforms show a manual path message)
