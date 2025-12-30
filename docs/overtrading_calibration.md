# Overtrading Calibration (PR39)

This document describes the SIM-only, deterministic calibration flow that derives regime-aware overtrading budgets from recent completed runs. The calibration is **advisory-only** and never places trades.

## What gets calibrated

Calibration builds per-regime distributions from recent completed runs (per `run_complete.json`) and derives budgets from observed data:

- **Trades per day (peak)**
- **Turnover per day**
- **Cooldown violations** (min-seconds-between-trades breach count)
- **Cost per trade**

Budgets are derived from observed distributions (default: **P90 caps** for trades/turnover/cost and **P10** for min-seconds-between-trades). No hardcoded thresholds are introduced.

## Regimes

The lightweight regime classifier (`python -m tools.regime_classifier`) uses replay decision-card prices to label the latest window into a small set:

- `TREND`
- `RANGE`
- `HIGH_VOL`
- `LOW_VOL`
- `INSUFFICIENT_DATA` (when the window or price series is too small)

Each regime report includes explainable metrics (rolling volatility, trend strength, ranks).

## Artifacts and pointers

Calibration and regime artifacts are repo-relative and deterministic:

- `artifacts/overtrading_calibration.json`
- `Logs/train_runs/_latest/overtrading_calibration_latest.json`
- `artifacts/regime_report.json`
- `Logs/train_runs/_latest/regime_report_latest.json`

The trade-activity audit links these artifacts in `trade_activity_report_latest.json` so the UI, Doctor, promotion gate, and XP model can reference them.

## INSUFFICIENT_DATA

If sample sizes are too small (default: `< 5` per regime), the calibration output sets:

- `status: INSUFFICIENT_DATA`
- `regimes.<REGIME>.insufficient_data: true`

Promotion gates can be configured to **fail-closed** when calibration is required but missing or insufficient. XP and Doctor will show the missing/insufficient status explicitly.

## Commands

Run the classifier and calibration manually (repo-relative paths only):

```
python -m tools.regime_classifier
python -m tools.overtrading_calibrate
```

To write a specific artifacts location:

```
python -m tools.overtrading_calibrate --artifacts-output artifacts/overtrading_calibration.json --latest-output Logs/train_runs/_latest/overtrading_calibration_latest.json
```

## Interpreting budgets

Budget values represent conservative caps derived from the observed distributions for each regime. They are meant to highlight when trade activity is **higher** than recent SIM evidence for the same regime, not to recommend any trading action.
