MEMORY_COMMIT:
- (autofix) This file is part of the canonical project constraints.

# Gates

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
- Archived events: opt-in validation via `--include-event-archives` (default skips legacy `events_YYYY-MM-DD.jsonl`).
- Legacy gates: opt-in via `--include-legacy-gates` (default skips `verify_pr20_gate.py`).
- Rationale: legacy artifacts should not block main by default while remaining auditable on demand.

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
