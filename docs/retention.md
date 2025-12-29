# Retention (SIM-only, READ_ONLY)

## Policy defaults

The seed policy lives at `Data/retention_policy.json` and is loaded deterministically at runtime. Key defaults:

- `keep_days_train_runs`: keep runs newer than this age (days)
- `keep_runs_max`: keep at most this many runs (oldest eligible for pruning)
- `keep_days_replay`: replay retention window (run-level)
- `keep_replay_max_bytes_per_run`: max replay JSONL size per run
- `keep_diagnostics_days`: retention for `Logs/runtime/*`
- `keep_events_days`: optional retention horizon for `Logs/events_*.jsonl` (not deleted in SAFE mode)
- `prune_mode`: default `SAFE`
- `recent_runs_buffer`: minimum number of newest runs always protected
- `recent_index_max_runs`: max entries in `Logs/train_runs/recent_runs_index.json`

## Runtime overrides (environment variables)

Override the policy without editing the seed file:

- `RETENTION_KEEP_DAYS_TRAIN_RUNS`
- `RETENTION_KEEP_RUNS_MAX`
- `RETENTION_KEEP_DAYS_REPLAY`
- `RETENTION_KEEP_REPLAY_MAX_BYTES`
- `RETENTION_KEEP_DIAGNOSTICS_DAYS`
- `RETENTION_KEEP_EVENTS_DAYS`
- `RETENTION_PRUNE_MODE`
- `RETENTION_RECENT_RUNS_BUFFER`
- `RETENTION_RECENT_INDEX_MAX_RUNS`

## Report

Generate a retention report (writes `Logs/runtime/retention_report.json`, and `artifacts/retention_report.json` in CI):

```
.\.venv\Scripts\python.exe -m tools.retention_engine report
```

The report includes:

- `policy` (resolved policy)
- `storage_summary` (bytes per category + free space when available)
- `candidates` (eligible items with reasons, size, age)
- `safety_checks` (latest pointers protected + required files readable)

## SAFE prune

SAFE pruning is conservative and evidence-first:

- Deletes only items older than `keep_days_*` **and** beyond `keep_runs_max`.
- Always protects the most recent `recent_runs_buffer` runs.
- Never deletes any `_latest` pointer files or files referenced by current pointers.
- Does **not** delete events by default (events are advisory-only unless you opt in outside SAFE mode).
- Writes `retention_prune_plan.json` before applying changes and `retention_prune_result.json` afterward.

Run SAFE prune (local only):

```
.\.venv\Scripts\python.exe -m tools.retention_engine prune --mode safe
```

## Recent runs index

`Logs/train_runs/recent_runs_index.json` keeps the Replay UI fast by avoiding full scans. Rebuild it safely:

```
.\.venv\Scripts\python.exe -m tools.recent_runs_index
```

The Action Center also exposes **RUN_RETENTION_REPORT**, **PRUNE_OLD_RUNS_SAFE**, and **REBUILD_RECENT_INDEX** as SAFE actions.
