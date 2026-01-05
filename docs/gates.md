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
   - PASS: docs/inventory.md matches generator output after LF normalization.
   - FAIL: inventory docs mismatch, missing, or UTF-8 BOM detected.
10) **verify_execution_model** (`python -m tools.verify_execution_model --artifacts-dir artifacts`)
   - PASS: execution model report generated and sensitivity stable.
   - FAIL: missing artifacts or friction sensitivity instability.
11) **apply_edits_dry_run** (`python -m tools.apply_edits --repo . --edits fixtures/edits_contract/good.json --artifacts-dir artifacts --dry-run`)
   - PASS: edits dry-run succeeded.
   - FAIL: edits dry-run failed.
12) **extract_json_strict_negative** (`python -m tools.extract_json_strict --raw-text fixtures/extract_json_strict/bad_fenced.txt --out-json artifacts/extract_json_strict_bad.json`)
   - PASS: gate fails as expected on bad input.
   - FAIL: unexpected success on bad input.
13) **verify_pr36_gate** (preflight, if present)
   - PASS: preflight gate OK.
   - FAIL: preflight gate failed.
14) **import_contract** (`python -m tools.verify_import_contract --module <canonical_gate> --artifacts-dir artifacts`)
   - PASS: canonical gate module imports.
   - FAIL: import contract failed.
15) **canonical gate runner** (one of the following):
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
- Runs: docs_contract, inventory_contract, execution_model plus basic sanity checks.
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

## Execution Model Gate (P0)
Deterministic execution-friction verification with sensitivity checks.
- Gate: `python -m tools.verify_execution_model --artifacts-dir artifacts`
- Input: defaults to `Data/quotes.csv`, falls back to `fixtures/quotes_sample.csv` when missing.
- Artifacts:
  - `artifacts/execution_model_report.json`
  - `artifacts/execution_model_report.txt`
- Output marker:
  - `EXECUTION_MODEL_SUMMARY|status=PASS/FAIL|reason=...|report=...`
- Fail-closed if ranking instability is detected between baseline and doubled friction scenarios.

## PR Ready Gate (P0)
Single deterministic signal for local PR readiness (fail-closed).
- Gate: `python -m tools.verify_pr_ready --artifacts-dir artifacts`
- Runs (in order):
  - `python -m tools.compile_check --targets tools scripts tests --artifacts-dir artifacts`
  - `python -m tools.verify_docs_contract --artifacts-dir artifacts`
  - `python -m tools.verify_inventory_contract --artifacts-dir artifacts`
  - `python -m tools.verify_execution_model --artifacts-dir artifacts`
  - `python -m tools.verify_foundation --artifacts-dir artifacts`
  - `python -m tools.verify_consistency --artifacts-dir artifacts` (PASS/DEGRADED allowed)
- Artifacts:
  - `artifacts/pr_ready_summary.json`
  - `artifacts/pr_ready.txt`
  - `artifacts/pr_ready_gates.log`
- Output markers:
  - `PR_READY_START`
  - `PR_READY_GATE|name=...|status=PASS/FAIL/DEGRADED|exit=...`
  - `PR_READY_SUMMARY|status=PASS/FAIL/DEGRADED|failed=N|degraded=M|next=...`
  - `PR_READY_END`

## Safe Push (Windows Local)
Use the safe push wrapper to prevent broken local pushes (fail-closed).

### When to pull vs push
- Cloud/Codex PRs: after merge, you **pull** updates locally.
- Local-model PRs (Windows): you **push** from Windows, but only through the safe wrapper.
  - Reminder: after a cloud merge, run `git pull` locally to sync files.

### Always use the safe push wrapper
Run the safe push script from repo root:
`.\scripts\safe_push_v1.ps1`

Optional overrides:
`.\scripts\safe_push_v1.ps1 -Remote origin -Branch HEAD`

The wrapper runs the PR_READY gate before printing the next command and emits stable markers:
- `SAFE_PUSH_START|...`
- `SAFE_PUSH_GATE|name=...|status=PASS/FAIL|log=...`
- `SAFE_PUSH_SUMMARY|status=PASS/FAIL|reason=...|next=...`
- `SAFE_PUSH_END`

The safe push wrapper is **print-only**. It never executes `git push`; instead it prints:
`next=git push -u origin <branch>`

## Safe Pull (Windows Local)
Use the safe pull wrapper to prevent broken local pulls (fail-closed).

### Always use the safe pull wrapper
Run the safe pull script from repo root:
`.\scripts\safe_pull_v1.ps1`

Optional overrides:
`.\scripts\safe_pull_v1.ps1 -Remote origin -Branch main`

The wrapper enforces:
- Repo root only (fails if not at repo root).
- Clean worktree (`git status --porcelain` is empty).
- No unmerged paths (`git ls-files -u` is empty).
- No in-progress git states (MERGE_HEAD, CHERRY_PICK_HEAD, REVERT_HEAD, rebase-apply, rebase-merge, AM).
- Fast-forward only (`git pull --ff-only`).

Markers emitted:
- `SAFE_PULL_START|...`
- `SAFE_PULL_SUMMARY|status=PASS/FAIL|reason=...|next=...`
- `SAFE_PULL_END`

## PowerShell Runner Contract (Windows)
All Windows command runners must use the canonical helper: `scripts/powershell_runner.ps1`.
It enforces deterministic exit codes, repo-root guardrails, and always writes artifacts.

Markers emitted (stdout + `artifacts/ps_run_markers.txt`):
- `PS_RUN_START|...`
- `PS_RUN_SUMMARY|status=PASS/FAIL|reason=...|exit_code=...|stdout=...|stderr=...`
- `PS_RUN_END`

Required artifacts (always written, even on failure):
- `artifacts/ps_run_summary.json`
- `artifacts/ps_run_stdout.txt`
- `artifacts/ps_run_stderr.txt`
- `artifacts/ps_run_markers.txt`

Debug workflow:
1) Inspect `artifacts/ps_run_summary.json` for status, exit code, and paths.
2) Review stdout/stderr files for command output.
3) Verify the calling script dot-sources the helper and uses `Invoke-PsRunner`.

### Strict JSON-only contract (v1)
Local model outputs that drive edits may be either:
- A full v1 payload object (current behavior), or
- Edits-only JSON (`{"edits":[...]}` or `[{...}, ...]`), which is scaffolded into a full payload.

Full v1 payload format:
```json
{
  "version": "v1",
  "created_at": "YYYY-MM-DDTHH:MM:SSZ or YYYY-MM-DDTHH:MM:SS±HH:MM",
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
- `created_at` is ISO8601 with timezone (UTC `Z` or offset).
Scaffolding:
- Edits-only JSON is wrapped via `python -m tools.scaffold_edits_payload`.
- Metadata fields are deterministically populated (timestamp in America/New_York).

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
- `scaffold_edits_payload.json` / `scaffold_edits_payload.txt` (scaffolded payload + summary)
- `verify_edits_payload.txt` / `verify_edits_payload.json`
- `apply_edits_result.json`
- `run_local_model_summary_<timestamp>.txt`

### Common failure reasons
- `markdown_fence_detected`: output contains ``` fences.
- `leading_prose_detected`: output starts with non-JSON text.
- `multiple_json_objects`: more than one JSON object found.
- `missing_version` / `missing_edits`: required keys absent.
- `edits_not_array`: edits key is not an array.
- `scaffold_edits_payload_failed`: edits-only scaffolding failed (see scaffold artifacts).
First artifact to open: `artifacts/verify_edits_contract.txt` (gate) or `artifacts/apply_edits_error.txt` (apply).
