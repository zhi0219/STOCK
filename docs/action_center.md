# Action Center (SIM-only, READ_ONLY)

## What it does

The Action Center provides a deterministic, auditable loop to diagnose missing or stale artifacts, propose safe recovery actions, and (optionally) execute those actions with typed confirmation. It is **SIM-only** and **READ_ONLY** with respect to external accounts; it never places trades, connects to brokers, or modifies funds.

## Artifacts

The Action Center report is emitted as JSON with a stable schema:

- `artifacts/action_center_report.json` (UI + CI evidence pack)

Related CI evidence artifacts:

- `artifacts/proof_summary.json`
- `artifacts/gates.log`
- `artifacts/action_center_apply_result.json`

## Safety model

- Read-only by default (report generation only).
- Any action requires a typed confirmation token.
- Actions are blocked in CI environments.
- Fail-closed: action execution fails on any error and logs an audit event.
- Action Center apply requires the per-action confirmation token (e.g., `REBUILD`) and supports `--dry-run` for evidence-only runs.

## Apply (local CLI)

List safe actions:

```
.\.venv\Scripts\python.exe -m tools.action_center_apply
```

Dry-run an action (no mutations, evidence only):

```
.\.venv\Scripts\python.exe -m tools.action_center_apply --action-id ACTION_REBUILD_PROGRESS_INDEX --confirm REBUILD --dry-run
```

Apply an action with confirmation (SIM-only, local):

```
.\.venv\Scripts\python.exe -m tools.action_center_apply --action-id ACTION_REBUILD_PROGRESS_INDEX --confirm REBUILD
```

Evidence and events:

- Evidence pack defaults to `artifacts/action_center_apply/` with:
  - `action_center_apply_summary.json`
  - `action_center_apply.log`
- Apply also writes `artifacts/action_center_apply_result.json`.
- Apply attempts append to `Logs/events_YYYY-MM-DD.jsonl`.

## CI forced-fail demo

When `CI_FORCE_FAIL=1` is used for the workflow dispatch demo, the Action Center still writes `artifacts/action_center_report.json` and records a `CI_FORCE_FAIL` issue in the report so the evidence pack remains complete.

## UI location

The Tk UI exposes an **Action Center** tab with:

- Status strip (`ACTION_CENTER_STATUS`, `DATA_HEALTH`, `LAST_REPORT_TS_UTC`, `LAST_APPLY_TS_UTC`)
- Action list with selection and recommended commands
- Apply workflow with confirmation token + SIM-only acknowledgment
- Doctor mini-panel for repo/venv/path checks
