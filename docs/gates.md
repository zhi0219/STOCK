MEMORY_COMMIT:
- (autofix) This file is part of the canonical project constraints.

# Gates

## Foundation Gate (P0)
One-shot, fail-closed aggregator gate used by CI and UI.
- Runs: docs_contract, pr_template_contract, defensive_redteam, windows_smoke, import-smoke (tools.ui_app)
- Outputs: artifacts/foundation_summary.json + per-step logs under artifacts/
- Rule: unknown => FAIL with an artifacts path to inspect

## Edits Contract Gate (P0)
Validates strict JSON-only edits outputs.
- Gate: `python -m tools.verify_edits_contract --artifacts-dir artifacts`
- Artifacts:
  - `artifacts/verify_edits_contract.txt`
  - `artifacts/verify_edits_contract.json`

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

### Common failure reasons
- `markdown_fence_detected`: output contains ``` fences.
- `leading_prose_detected`: output starts with non-JSON text.
- `multiple_json_objects`: more than one JSON object found.
- `missing_version` / `missing_edits`: required keys absent.
- `edits_not_array`: edits key is not an array.
First artifact to open: `artifacts/verify_edits_contract.txt` (gate) or `artifacts/apply_edits_error.txt` (apply).
