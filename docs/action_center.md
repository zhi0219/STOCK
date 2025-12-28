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

## CI forced-fail demo

When `CI_FORCE_FAIL=1` is used for the workflow dispatch demo, the Action Center still writes `artifacts/action_center_report.json` and records a `CI_FORCE_FAIL` issue in the report so the evidence pack remains complete.

## UI location

The Tk UI exposes an **Action Center** panel under **Progress (SIM-only)** with:

- Latest report summary
- Buttons for the three safe recovery actions
- “Open latest evidence pack” convenience button (Windows only; other platforms show a manual path message)
