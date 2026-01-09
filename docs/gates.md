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
5) **verify_repo_doctor_contract** (`python -m tools.verify_repo_doctor_contract --artifacts-dir artifacts`)
   - PASS: repo doctor contract intact.
   - FAIL: required markers or command guardrails missing.
6) **verify_win_daily_green_contract** (`python -m tools.verify_win_daily_green_contract --artifacts-dir artifacts`)
   - PASS: daily green entrypoint contract intact.
   - FAIL: required markers or command guardrails missing.
7) **verify_write_allowlist_contract** (`python -m tools.verify_write_allowlist_contract --artifacts-dir artifacts`)
   - PASS: daily scripts only write under artifacts.
   - FAIL: detected write outside allowlist.
8) **powershell_join_path_contract** (`python -m tools.verify_powershell_join_path_contract --artifacts-dir artifacts`)
   - PASS: PowerShell Join-Path usage is PowerShell 5.1-safe.
   - FAIL: any Join-Path call uses 3+ positional arguments or `-AdditionalChildPath`.
   - Rule: use nested `Join-Path` or `[IO.Path]::Combine` for 3+ segments.
9) **verify_powershell_null_safe_trim_contract** (`python -m tools.verify_powershell_null_safe_trim_contract --artifacts-dir artifacts`)
   - PASS: PowerShell Trim only uses null-safe patterns (`[string]::Concat(...).Trim()` or explicit null-guard before `.Trim()`).
   - FAIL: any `(... + ...).Trim()` concatenation, even with explicit casts.
   - Rule: prefer `[string]::Concat(...)` before `.Trim()` or guard nulls before trimming.
   - Artifacts: `artifacts/verify_powershell_null_safe_trim_contract.json`, `artifacts/verify_powershell_null_safe_trim_contract.txt`.
10) **verify_powershell_no_goto_labels_contract** (`python -m tools.verify_powershell_no_goto_labels_contract --artifacts-dir artifacts`)
   - PASS: no PowerShell goto statements or bare label lines detected.
   - FAIL: any line starts with `goto` or is a bare `:Label`.
11) **ui_preflight** (`python -m tools.ui_preflight --ci --artifacts-dir artifacts`)
   - PASS: UI preflight OK.
   - FAIL: preflight error.
12) **docs_contract** (`python -m tools.verify_docs_contract --artifacts-dir artifacts`)
   - PASS: required docs and sections present.
   - FAIL: missing docs/sections/IMP list.
13) **verify_edits_contract** (`python -m tools.verify_edits_contract --artifacts-dir artifacts`)
   - PASS: edits contract valid.
   - FAIL: edits contract violation.
14) **inventory_repo** (`python -m tools.inventory_repo --artifacts-dir artifacts --write-docs`)
   - PASS: inventory artifacts and docs generated.
   - FAIL: inventory generation failed.
15) **verify_inventory_contract** (`python -m tools.verify_inventory_contract --artifacts-dir artifacts`)
   - PASS: docs/inventory.md matches canonical generator output (UTF-8 no BOM, LF-only, POSIX paths).
   - FAIL: inventory docs mismatch, missing, BOM, CRLF, or backslash paths detected.
16) **verify_execution_model** (`python -m tools.verify_execution_model --artifacts-dir artifacts`)
   - PASS: execution model report generated and sensitivity stable.
   - FAIL: missing artifacts or friction sensitivity instability.
17) **verify_data_health** (`python -m tools.verify_data_health --artifacts-dir artifacts`)
   - PASS: data health report generated with no critical anomalies.
   - FAIL: integrity checks failed (monotonicity, parse errors, missingness, or jump detection).
18) **verify_walk_forward** (`python -m tools.verify_walk_forward --artifacts-dir artifacts`)
   - PASS: walk-forward report generated with non-zero embargo and baseline comparisons.
   - FAIL: missing windows, zero/invalid embargo, or missing artifacts.
19) **verify_redteam_integrity** (`python -m tools.verify_redteam_integrity --artifacts-dir artifacts`)
   - PASS: red-team cases detected expected failures and control passed.
   - FAIL: unexpected pass/fail in any case or missing trial budget metadata.
20) **verify_multiple_testing_control** (`python -m tools.verify_multiple_testing_control --artifacts-dir artifacts`)
   - PASS: experiment ledger present, baseline coverage intact, and trials within budget (or override recorded).
   - FAIL: missing ledger fields, missing baselines, or trial budget exceeded without override.
21) **apply_edits_dry_run** (`python -m tools.apply_edits --repo . --edits fixtures/edits_contract/good.json --artifacts-dir artifacts --dry-run`)
   - PASS: edits dry-run succeeded.
   - FAIL: edits dry-run failed.
22) **extract_json_strict_negative** (`python -m tools.extract_json_strict --raw-text fixtures/extract_json_strict/bad_fenced.txt --out-json artifacts/extract_json_strict_bad.json`)
   - PASS: gate fails as expected on bad input.
   - FAIL: unexpected success on bad input.
23) **verify_pr36_gate** (preflight, if present)
   - PASS: preflight gate OK.
   - FAIL: preflight gate failed.
24) **import_contract** (`python -m tools.verify_import_contract --module <canonical_gate> --artifacts-dir artifacts`)
   - PASS: canonical gate module imports.
   - FAIL: import contract failed.
25) **canonical gate runner** (one of the following):
   - `python tools/verify_prNN_gate.py` (highest available PR gate, e.g., verify_pr40_gate)
   - `python tools/verify_foundation.py` (fallback)
   - `python tools/verify_consistency.py` (fallback)
   - PASS: gate summary PASS/DEGRADED.
   - FAIL: gate summary FAIL or non-zero exit.

## Daily Green (Windows)
Stable Windows entrypoint for daily sync + health checks.
- Entrypoint: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/win_daily_green_v1.ps1 -RepoRoot . -ArtifactsDir artifacts`
- PASS criteria:
  - `DAILY_GREEN_SUMMARY|status=PASS` marker emitted.
  - Worktree clean both before and after (daily green fails closed otherwise).
  - Artifacts under `artifacts/<run_stamp>/` with `safe_pull/` and `repo_doctor/` subfolders.
- FAIL criteria:
  - Any non-zero ExitCode in safe pull or repo doctor, or dirty worktree before/after.
  - Summary includes `next=...` pointing at the next inspection step.
- Notes:
  - `repo_doctor_v1.ps1` defaults to `-WriteDocs NO` (non-mutating).
  - Use `scripts/win_inventory_refresh_v1.ps1` for the explicit, mutating inventory refresh workflow.

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
- Runs: docs_contract, inventory_contract, execution_model, data_health, walk_forward, redteam_integrity, multiple_testing_control plus basic sanity checks.
- Default status: PASS/FAIL based on required gates only (optional checks are opt-in).
- Archived events:
  - Canonical location: `Logs/event_archives/`.
  - Legacy location: `Logs/_event_archives/` (migrate to canonical when present).
  - Opt-in validation: `python -m tools.verify_consistency --include-event-archives --artifacts-dir artifacts`.
  - When opted in with zero archive files, reports `archive_files=0` as OK.
  - Migration command: `python -m tools.migrate_event_archives --logs-dir Logs --archive-dir Logs/event_archives --artifacts-dir artifacts --mode move`.
- Legacy gates: opt-in via `--include-legacy-gates` (default does not mention or count them).
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

## Data Health Gate (P0)
Timeseries integrity verification with deterministic anomaly checks.
- Gate: `python -m tools.verify_data_health --artifacts-dir artifacts`
- Inputs: optional `--data-path`, defaulting to fixtures when no dataset is present.
- Artifacts:
  - `artifacts/data_health_report.json`
  - `artifacts/data_health_report.txt`
- Output markers:
  - `DATA_HEALTH_START`
  - `DATA_HEALTH_SUMMARY|status=PASS/FAIL|...`
  - `DATA_HEALTH_END`
- FAIL triggers: timestamp parse errors, duplicates, monotonicity violations, missingness beyond threshold, extreme jumps.
- Warnings (PASS): non-trading segments such as zero-volume runs.

## Walk-Forward Gate (P0)
Deterministic rolling walk-forward evaluation with embargo/gap protection.
- Gate: `python -m tools.verify_walk_forward --artifacts-dir artifacts`
- Inputs: optional `--input` OHLCV CSV, window sizes (`--train-size`, `--gap-size`, `--test-size`, `--step-size`), and `--timezone`.
- Artifacts:
  - `artifacts/walk_forward_report.json`
  - `artifacts/walk_forward_report.txt`
  - `artifacts/walk_forward_windows.csv`
- Output markers:
  - `WALK_FORWARD_START`
  - `WALK_FORWARD_WINDOW|idx=...|train=...|gap=...|test=...|status=...`
  - `WALK_FORWARD_SUMMARY|status=PASS/FAIL|windows=...|baselines=...|notes=...`
  - `WALK_FORWARD_END`
- FAIL triggers: zero/invalid embargo, no windows produced, missing baselines, or missing artifacts.

## Red-Team Integrity Gate (P0)
Fail-closed red-team suite for leakage, misalignment, and survivorship bias.
- Gate: `python -m tools.verify_redteam_integrity --artifacts-dir artifacts`
- Inputs: deterministic fixtures under `fixtures/redteam_integrity/`.
- Artifacts:
  - `artifacts/redteam_report.json`
  - `artifacts/redteam_report.txt`
  - `artifacts/redteam_<case>.txt`
- Output markers:
  - `REDTEAM_START`
  - `REDTEAM_CASE|name=...|status=...|expected=...|detail=...`
  - `REDTEAM_SUMMARY|status=PASS/FAIL|cases=...|unexpected_passes=...|unexpected_failures=...|detail=...|report=...`
  - `REDTEAM_END`
- FAIL triggers: any unexpected pass/fail on a case, or missing trial budget metadata.

## Multiple-Testing Control Gate (P0)
Fail-closed governance guardrail for search-scale auditing.
- Gate: `python -m tools.verify_multiple_testing_control --artifacts-dir artifacts`
- Inputs: latest ledger pointer (`artifacts/experiment_ledger_latest.json`) pointing to a per-run ledger (for example, `artifacts/experiment_ledger_<run_id>.jsonl`), plus `fixtures/multiple_testing_control/trial_budget.json`.
- Artifacts:
  - `artifacts/experiment_ledger_summary.json`
- Output markers:
  - `MULTITEST_START`
  - `MULTITEST_CASE|name=...|status=...|detail=...`
  - `MULTITEST_SUMMARY|status=PASS/FAIL|trial_count=...|candidate_count=...|requested_trial_count=...|requested_candidate_count=...|enforced_trial_count=...|enforced_candidate_count=...|penalty=...|detail=...|report=...`
  - `MULTITEST_END`
- FAIL triggers: missing ledger fields, missing baselines (DoNothing, Buy&Hold, SimpleMomentum), or trial budget exceeded without override.
- Enforcement:
  - Candidate generation and tournament scheduling clamp to the trial budget (both candidate_count and total trial_count).
  - When upstream requests exceed budget, the run clamps and records enforcement artifacts/markers; if the budget is below baseline coverage, the run fails with `next=reduce search scale`.

## Runtime data zone policy
- `Data/` remains controlled (tracked or governed fixtures only).
- `Logs/data_runtime/` is an approved runtime-only zone for transient data (ignored by git and allowed by repo hygiene).

## PR Ready Gate (P0)
Single deterministic signal for local PR readiness (fail-closed).
- Gate: `python -m tools.verify_pr_ready --artifacts-dir artifacts`
- Runs (in order):
  - `python -m tools.compile_check --targets tools scripts tests --artifacts-dir artifacts`
  - `python -m tools.verify_docs_contract --artifacts-dir artifacts`
  - `python -m tools.verify_inventory_contract --artifacts-dir artifacts`
  - `python -m tools.verify_execution_model --artifacts-dir artifacts`
  - `python -m tools.verify_foundation --artifacts-dir artifacts`
- `python -m tools.verify_consistency --artifacts-dir artifacts` (PASS/FAIL only; optional checks require explicit flags)
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

AutoStash override (explicit opt-in, fail-closed by default):
`.\scripts\safe_pull_v1.ps1 -AutoStash YES`

The wrapper enforces:
- Repo root only (fails if not at repo root).
- Clean worktree (`git status --porcelain` is empty).
- No unmerged paths (`git ls-files -u` is empty).
- No in-progress git states (MERGE_HEAD, CHERRY_PICK_HEAD, REVERT_HEAD, rebase-apply, rebase-merge, AM).
- Fast-forward only (`git pull --ff-only`).

AutoStash behavior when `-AutoStash YES` and a dirty worktree is detected:
- Stashes tracked + untracked changes with `git stash push -u` and a UTC timestamp label.
- Re-checks status; if still dirty, fails closed.
- Runs the fast-forward pull.
- On pull success, preserves the stash (prints a NEXT marker).
- On pull failure, attempts `git stash pop` to roll back and records the rollback status.

Markers emitted:
- `SAFE_PULL_START|...`
- `SAFE_PULL_AUTOSTASH_START|...`
- `SAFE_PULL_AUTOSTASH_SUMMARY|status=...|stash_created=...|pull_status=...|rollback_status=...|next=...`
- `SAFE_PULL_AUTOSTASH_END`
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
