# PR28 Training Loop (SIM-only)

## Scope and safety

- **SIM-only / READ_ONLY**: this loop never places orders, never connects to brokers, and only uses local historical data.
- Deterministic, auditable artifacts are emitted under `Logs/train_runs/` with `_latest` pointers for the UI and CI gates.

## What the artifacts mean

Each run writes versioned artifacts under `Logs/train_runs/<run_id>/` and updates `_latest` pointers under `Logs/train_runs/_latest/`:

- `tournament_result.json`: deterministic tournament results for a small candidate set vs baselines. Includes entries, matchups, metrics, seed, timestamps.
- `judge_result.json`: evaluation vs baselines with explicit `INSUFFICIENT_DATA` when required. Includes thresholds and reasons.
- `promotion_decision.json`: conservative promotion gate decision (APPROVE/REJECT) with reasons and thresholds.
- `promotion_history.jsonl`: append-only history events (also mirrored at `Logs/train_runs/promotion_history.jsonl`).

All JSON artifacts include `schema_version`, `ts_utc`, `run_id`, and `git_commit` (when available).

## How to run locally (module mode)

Run the PR28 training loop:

```
python -m tools.pr28_training_loop --tiny
```

Run the PR28 gate (fast deterministic flow with schema validation):

```
python -m tools.verify_pr28_gate
```

To force a controlled failure while still emitting artifacts:

```
PR28_FORCE_FAIL=1 ./scripts/ci_gates.sh
```

## Evidence pack locations

The latest pointers are stored here:

- `Logs/train_runs/_latest/tournament_result_latest.json`
- `Logs/train_runs/_latest/judge_result_latest.json`
- `Logs/train_runs/_latest/promotion_decision_latest.json`
- `Logs/train_runs/_latest/promotion_history_latest.json`

Use these paths in the UI Progress panel under **PR28 Training Loop (SIM-only)**.
