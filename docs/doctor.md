# Doctor (SIM-only, READ_ONLY)

The Doctor report turns common “UI looks broken / state write failed / repo dirty” situations into an auditable,
replayable loop:

**Doctor → Actions → Apply → Evidence**

Doctor is advisory-only. It never places trades, talks to brokers, or modifies funds.

## What Doctor checks

- **Kill switch presence** and location.
- **Runtime atomic write health** (Logs/runtime write probe with bounded retries).
- **Stale temp files** (e.g., `*.tmp`) older than the threshold.
- **Repo hygiene summary** (tracked/untracked/ignored counts via `tools.repo_hygiene`).
- **Absolute path leaks** in selected artifacts (Windows-style `C:\...` patterns).
- **Import/entrypoint sanity** (reuse `artifacts/import_contract_result.json` if present, otherwise lightweight import).

Doctor writes a stable schema to `artifacts/doctor_report.json`.

## Fix All (Safe): what it does

The Action Center **Fix All (Safe)** button runs SAFE actions in a deterministic order:

- `GEN_DOCTOR_REPORT`
- `ENSURE_RUNTIME_DIRS`
- `DIAG_RUNTIME_WRITE`
- `ABS_PATH_SANITIZE_HINT`
- `ENABLE_GIT_HOOKS` (best effort; may refuse if unsupported)

### What it does NOT do

- It **does not** clear kill switches.
- It **does not** delete stale temp files.
- It **does not** run repo hygiene fixes.
- It **does not** restart services.

All CAUTION/DANGEROUS actions require explicit UI confirmation and a press-and-hold.

## Interpreting artifacts

- `artifacts/doctor_report.json`: Full Doctor report and issues list.
- `artifacts/action_center_report.json`: Consolidated Action Center report with Doctor-derived actions.
- `artifacts/action_center_apply_plan.json`: Planned action (written before applying).
- `artifacts/action_center_apply_result.json`: Apply outcome (PASS/FAIL/REFUSED) and evidence.
- `artifacts/doctor_runtime_write.json`: Runtime write diagnostics (if executed).
- `artifacts/abs_path_sanitize_hint.json`: Path-sanitization guidance (if executed).
