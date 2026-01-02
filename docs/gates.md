TBD

MEMORY_COMMIT:
- (autofix) This file is part of the canonical project constraints.

## Foundation Gate (P0)
One-shot, fail-closed aggregator gate used by CI and UI.
- Runs: docs_contract, pr_template_contract, defensive_redteam, windows_smoke, import-smoke (tools.ui_app)
- Outputs: artifacts/foundation_summary.json + per-step logs under artifacts/
- Rule: unknown => FAIL with an artifacts path to inspect