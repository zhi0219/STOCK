MEMORY_COMMIT:
- (autofix) This file is part of the canonical project constraints.

# Gates

## PASS/FAIL semantics
PASS means the gate completed successfully with no missing artifacts and no unmet requirements.
DEGRADED means the gate completed but emitted non-fatal warnings (for example, optional dependencies missing).
FAIL means the gate did not meet requirements or could not complete. Failures are fail-closed.

PASS vs DEGRADED is defined exclusively by the gate's summary marker and must be auditable in artifacts.

## CI Gates (ordered)
Each gate must emit PASS/FAIL semantics and fail-closed by default.

1) **compile_check** (`python -m tools.compile_check --targets tools scripts --artifacts-dir artifacts`)
   - PASS: compile check OK.
   - FAIL: syntax or compile errors.
2) **syntax_guard** (`python -m tools.syntax_guard --artifacts-dir artifacts`)
   - PASS: no forbidden syntax.
   - FAIL: forbidden syntax detected.
3) **ps_parse_guard** (`python -m tools.ps_parse_guard --script scripts/run_ui_windows.ps1 --artifacts-dir artifacts`)
   - PASS: PowerShell parses cleanly.
   - FAIL: parse errors.
4) **safe_push_contract** (`python -m tools.safe_push_contract --artifacts-dir artifacts`)
   - PASS: safe push contract intact.
   - FAIL: required markers missing.
5) **ui_preflight** (`python -m tools.ui_preflight --ci --artifacts-dir artifacts`)
   - PASS: UI preflight OK.
   - FAIL: preflight error.
6) **docs_contract** (`python -m tools.verify_docs_contract --artifacts-dir artifacts`)
   - PASS: required docs and sections present.
   - FAIL: missing docs/sections/IMP list.
7) **verify_edits_contract** (`python -m tools.verify_edits_contract --artifacts-dir artifacts`)
   - PASS: edits contract valid.
   - FAIL: edits contract violation.
8) **inventory_repo** (`python -m tools.inventory_repo --artifacts-dir artifacts --write-docs`)
   - PASS: inventory artifacts and docs generated.
   - FAIL: inventory generation failed.
9) **verify_inventory_contract** (`python -m tools.verify_inventory_contract --artifacts-dir artifacts`)
   - PASS: docs/inventory.md matches generator output.
   - FAIL: inventory docs mismatch or missing.
10) **apply_edits_dry_run** (`python -m tools.apply_edits --repo . --edits fixtures/edits_contract/good.json --artifacts-dir artifacts --dry-run`)
   - PASS: edits dry-run succeeded.
   - FAIL: edits dry-run failed.
11) **extract_json_strict_negative** (`python -m tools.extract_json_strict --raw-text fixtures/extract_json_strict/bad_fenced.txt --out-json artifacts/extract_json_strict_bad.json`)
   - PASS: gate fails as expected on bad input.
   - FAIL: unexpected success on bad input.
12) **verify_pr36_gate** (preflight, if present)
   - PASS: preflight gate OK.
   - FAIL: preflight gate failed.
13) **import_contract** (`python -m tools.verify_import_contract --module <canonical_gate> --artifacts-dir artifacts`)
   - PASS: canonical gate module imports.
   - FAIL: import contract failed.
14) **canonical gate runner** (one of the following):
   - `python tools/verify_prNN_gate.py` (highest available PR gate, e.g., verify_pr40_gate)
   - `python tools/verify_foundation.py` (fallback)
   - `python tools/verify_consistency.py` (fallback)
   - PASS: gate summary PASS/DEGRADED.
   - FAIL: gate summary FAIL or non-zero exit.

## Foundation Gate (P0)
One-shot, fail-closed aggregator gate used by CI and UI.
- Runs: docs_contract, pr_template_contract, defensive_redteam, windows_smoke, import-smoke (tools.ui_app)
- Outputs: artifacts/foundation_summary.json + per-step logs under artifacts/
- Rule: unknown => FAIL with an artifacts path to inspect
- Windows note: zoneinfo relies on tzdata; install requirements.txt to avoid timezone failures.

## Edits Contract Gate (P0)
Validates strict JSON-only edits outputs.
- Gate: `python -m tools.verify_edits_contract --artifacts-dir artifacts`
- Artifacts:
  - `artifacts/verify_edits_contract.txt`
  - `artifacts/verify_edits_contract.json`

## Consistency Gate (P0)
Aggregates lightweight health checks for CI consistency.
- Gate: `python -m tools.verify_consistency --artifacts-dir artifacts`
- Archived events:
  - Canonical location: `Logs/event_archives/`.
  - Legacy location: `Logs/_event_archives/` (migrate to canonical when present).
  - Opt-in validation: `python -m tools.verify_consistency --include-event-archives --artifacts-dir artifacts`.
  - Migration command: `python -m tools.migrate_event_archives --logs-dir Logs --archive-dir Logs/event_archives --artifacts-dir artifacts --mode move`.
- Legacy gates: opt-in via `--include-legacy-gates` (default skips `verify_pr20_gate.py`).
- Rationale: legacy artifacts should not block main by default while remaining auditable on demand.
- Output contract (single-line ASCII markers):
  - PASS:
    - `CONSISTENCY_SUMMARY|status=PASS|...`
    - `CONSISTENCY_OK|status=PASS`
  - DEGRADED:
    - `CONSISTENCY_SUMMARY|status=DEGRADED|...`
    - `CONSISTENCY_OK_BUT_DEGRADED|skipped=<comma list>|next=review [SKIP] lines above (expected unless opt-in)|how_to_opt_in=--include-event-archives,--include-legacy-gates`
  - FAIL:
    - `CONSISTENCY_SUMMARY|status=FAIL|...`
    - `CONSISTENCY_FAIL|next=python tools/verify_consistency.py`
  - Only FAIL emits a next-action marker; PASS/DEGRADED must not print any "Next step:" line.

## Safe Push (Windows Local)
Use the safe push wrapper to prevent broken local pushes (fail-closed).

### When to pull vs push
- Cloud/Codex PRs: after merge, you **pull** updates locally.
- Local-model PRs (Windows): you **push** from Windows, but only through the safe wrapper.

### Always use the safe push wrapper
Run the safe push script from repo root:
`.\scripts\safe_push_v1.ps1`

Optional overrides:
`.\scripts\safe_push_v1.ps1 -Remote origin -Branch HEAD`

The wrapper runs mandatory gates before `git push` and emits stable markers:
- `SAFE_PUSH_START|...`
- `SAFE_PUSH_GATE|name=...|status=PASS/FAIL|log=...`
- `SAFE_PUSH_SUMMARY|status=PASS/FAIL|reason=...|next=...`
- `SAFE_PUSH_END`

### Strict JSON-only contract (v1)
All local model outputs that drive edits must be a single JSON object:
```json
{
  "version": "v1",
  "created_at": "YYYY-MM-DDTHH:MM:SSZ",
  "edits": [],
  "assumptions": [],
  "risks": [],
  "gates": [],
  "rollback": []
}
```
Rules:
- JSON only (no prose, no markdown fences).
- One object only (no concatenated JSON).
- `edits` is an array.
- `created_at` is ISO8601 UTC with `Z`.

### Minimal runbook
1) Generate raw output (untrusted).
2) Normalize: `python -m tools.normalize_edits --input <raw> --output artifacts/edits.normalized.json`
3) Dry-run apply: `.\scripts\apply_edits_v1.ps1 -RepoRoot <repo> -EditsPath artifacts\edits.normalized.json -DryRun`
4) Apply: `.\scripts\apply_edits_v1.ps1 -RepoRoot <repo> -EditsPath artifacts\edits.normalized.json`

### UI Local Model (Dry-Run) runbook
Use the UI panel to run the local model pipeline without modifying the repo:
1) Open `tools/ui_app.py` and click the **Local Model (Dry-Run)** tab.
2) Provide:
   - Model name (ollama local model ID).
   - Prompt path (file path relative to repo root or absolute).
   - Artifacts dir (default `artifacts/`).
3) Click **Run Local Model (Dry-Run)**.
4) Review marker lines:
   - `RUN_LOCAL_MODEL_START|...`
   - `VERIFY_EDITS_PAYLOAD_SUMMARY|...`
   - `APPLY_EDITS_SUMMARY|...`
   - `RUN_LOCAL_MODEL_SUMMARY|...`
   - `RUN_LOCAL_MODEL_END`
5) Use **Open artifacts folder** or **Copy artifact path** for evidence files.

Artifacts expected in the artifacts dir:
- `ollama_raw_<timestamp>.txt` (raw model output)
- `edits_<timestamp>.json` (extracted JSON edits)
- `verify_edits_payload.txt` / `verify_edits_payload.json`
- `apply_edits_result.json`
- `run_local_model_summary_<timestamp>.txt`

### Common failure reasons
- `markdown_fence_detected`: output contains ``` fences.
- `leading_prose_detected`: output starts with non-JSON text.
- `multiple_json_objects`: more than one JSON object found.
- `missing_version` / `missing_edits`: required keys absent.
- `edits_not_array`: edits key is not an array.
First artifact to open: `artifacts/verify_edits_contract.txt` (gate) or `artifacts/apply_edits_error.txt` (apply).

## Git Health (Read-only report + explicit fix)
The git health report is **read-only** and must never delete or modify files.

### Report (read-only)
Command:
`python -m tools.git_health report --artifacts-dir artifacts`

Markers:
- `GIT_HEALTH_START`
- `GIT_HEALTH_SUMMARY|status=PASS|reason=...|next=...`
- `GIT_HEALTH_END`

Artifacts:
- `artifacts/git_health_report.json`
- `artifacts/git_health_report.txt`

Exit codes:
- `0` on PASS
- non-zero on FAIL

### Fix (explicit + lock-safe)
Command:
`python -m tools.git_health fix --artifacts-dir artifacts`

Behavior:
- Only runs when explicitly invoked.
- Skips locked files (e.g., WinError 32) and records them in artifacts.
- Emits `status=DEGRADED` when locked files are skipped, with next steps to close locks and rerun.

Exit codes:
- `0` on PASS or DEGRADED
- non-zero on FAIL
