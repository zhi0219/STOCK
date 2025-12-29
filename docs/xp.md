# Truthful XP Specification (v1)

This document defines the deterministic XP/Level model computed from auditable SIM-only artifacts. XP is **advisory-only**, evidence-first, and fail-closed.

## Inputs (artifacts only)

XP is derived exclusively from these artifacts:

- PR28 training loop artifacts:
  - `Logs/train_runs/_latest/tournament_result_latest.json`
  - `Logs/train_runs/_latest/judge_result_latest.json`
  - `Logs/train_runs/_latest/promotion_decision_latest.json`
  - `Logs/train_runs/_latest/promotion_history_latest.json` (and its `history_path` JSONL when present)
- Walk-forward evaluation:
  - `Logs/runtime/walk_forward/_latest/walk_forward_result_latest.json`
  - `Logs/runtime/walk_forward/_latest/walk_forward_windows_latest.jsonl`
- Doctor report (safety evidence):
  - `artifacts/doctor_report.json`
- Repo hygiene (optional, if present):
  - `artifacts/repo_hygiene.json`

When any required inputs are missing or malformed, XP fails closed with `INSUFFICIENT_DATA` penalties and explicit missing reasons.

## XP outputs (authoritative fields)

XP is written as a snapshot artifact:

- `xp_spec_version`: `"v1"`
- `xp_total`: integer
- `level`: integer
- `level_progress`: float in `[0, 1]`
- `xp_breakdown`: list of line items
  - `{ key, label, value, points, evidence_paths_rel[], notes? }`
- `status`: `OK` or `INSUFFICIENT_DATA`
- `missing_reasons`: array of missing reason strings

## Scoring dimensions (v1)

### 1) Advantage vs baselines (judge_result deltas)

Source: `judge_result_latest.json → scores.advantages`

For each baseline:

- `baseline_do_nothing` → "DoNothing"
- `baseline_buy_hold` → "Buy&Hold"

Points formula:

```
points = clamp(round(delta * 100), -40, 60)
```

Positive deltas earn points; negative deltas reduce points.

### 2) Risk discipline (drawdown/volatility)

Source: `tournament_result_latest.json → entries[].metrics`

- Drawdown penalty:
  ```
  penalty = clamp(-round(max(0, max_drawdown_pct - 5.0) * 2), -30, 0)
  ```
- Volatility proxy penalty (if present):
  ```
  penalty = clamp(-round(max(0, volatility_proxy - 0.02) * 200), -20, 0)
  ```

Missing metrics produce `INSUFFICIENT_DATA` penalties.

### 3) Stability (history consistency proxy)

Source: `promotion_history_latest.json` + JSONL history (if available).

If at least 3 history events exist, evaluate the most recent 5 decisions:

- All decisions identical → `+10` points
- Mixed decisions → `-5` points

If insufficient history, add `INSUFFICIENT_DATA` penalty.

### 4) Safety compliance

Source: `artifacts/doctor_report.json` and optional `artifacts/repo_hygiene.json`

Points:

- Kill switch **CLEAR** → `+5`, **TRIPPED** → `-15`
- `runtime_write_health.status == PASS` → `+5`, else `-10`
- Repo hygiene status **PASS** → `+5`, else `-10`

Missing safety evidence adds `INSUFFICIENT_DATA` penalties.

### 5) Overtrading guardrails

Source: `Logs/train_runs/_latest/trade_activity_report_latest.json`

- Any trade-activity violations or `status != PASS` → explicit penalty line item.
- Missing trade-activity evidence adds an `INSUFFICIENT_DATA` penalty.

### 6) Walk-forward stability

Source: `walk_forward_result_latest.json`

- PASS with sufficient window passes → `+10` points
- FAIL or insufficient windows → `-10` points

Missing walk-forward evidence adds an `INSUFFICIENT_DATA` penalty.

## Level function (v1)

Levels are computed from cumulative thresholds:

```
LEVEL_THRESHOLDS = [0, 100, 250, 450, 700, 1000, 1400, 1850, 2350]
```

- Level starts at **1** for XP ≥ 0.
- `level_progress` measures the fraction between the current level threshold and the next.

`xp_total` is clamped at 0 for level computation.

## Recompute XP (deterministic)

Use module mode for reproducible runs:

```
python -m tools.write_xp_snapshot
```

This writes:

- `Logs/train_runs/progress_xp/xp_snapshot_<timestamp>.json`
- `Logs/train_runs/progress_xp/xp_snapshot_latest.json`
- `artifacts/xp_snapshot.json` (CI copy)

## UI mapping (no fiction)

The Progress panel renders XP **only** from `xp_snapshot_latest.json`. The breakdown table uses the snapshot line items directly, and the “Open XP Evidence” action points to the snapshot folder or shows the repo-relative evidence paths embedded in the snapshot.
