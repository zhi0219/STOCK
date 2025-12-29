# Walk-forward evaluation

## Purpose

The walk-forward harness provides deterministic, no-lookahead evaluation by splitting data into
sequential train/eval windows. Each window trains on the past slice and evaluates on the next
slice with explicit, recorded boundaries.

## Artifacts

The evaluator writes versioned artifacts per run and `_latest` pointers:

- `Logs/train_runs/<run_id>/walk_forward_result.json`
- `Logs/train_runs/<run_id>/walk_forward_windows.jsonl`
- `Logs/train_runs/_latest/walk_forward_result_latest.json`
- `Logs/train_runs/_latest/walk_forward_windows_latest.jsonl`

When run without `--no-artifacts`, it also emits:

- `artifacts/walk_forward_result.json`
- `artifacts/walk_forward_windows.jsonl`

Each window record includes:

- `ts_utc`, `window_id`
- `train_start_index`, `train_end_index`, `eval_start_index`, `eval_end_index`
- `train_start_ts`, `train_end_ts`, `eval_start_ts`, `eval_end_ts`
- `candidate_id`, `candidate_score`, `baseline_scores`, `baseline_beats`
- `metrics` and pass/fail status

## Invariants

- Windows are evaluated sequentially (train window followed by its eval window).
- Window boundaries are explicit in every record.
- No-lookahead invariants are validated separately by `tools/no_lookahead_audit.py`.

## Usage

```
python -m tools.walk_forward_eval
```

Tiny mode for CI:

```
python -m tools.walk_forward_eval --tiny
```
