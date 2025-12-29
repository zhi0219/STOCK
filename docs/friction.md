# Execution Friction (SIM-only)

This repo uses a deterministic, SIM-only execution friction model to avoid overstating paper performance.
The model applies fees, spread/slippage, latency, and optional partial fills in a reproducible way.

## Policy file

The default policy lives at:

- `Data/friction_policy.json`

Fields:

- `fee_per_trade`: flat fee per filled order
- `fee_per_share`: per-share fee
- `spread_bps`: baseline spread in basis points
- `slippage_bps`: baseline slippage in basis points
- `latency_ms`: simulated execution latency in milliseconds
- `partial_fill_prob`: probability of a partial fill (default `0.0`)
- `max_fill_fraction`: cap on fill fraction when partial fills are enabled

## Determinism

`tools.execution_friction.apply_friction(...)` is deterministic by default. Randomness is only used when:

- `partial_fill_prob > 0`, and
- a seeded `rng_seed` is provided.

Stress scenario C enables partial fills with a deterministic seed so results remain repeatable.

## Stress artifacts

The stress harness writes evidence for baseline and stress scenarios:

- `Logs/train_runs/<run_id>/stress_report.json`
- `Logs/train_runs/<run_id>/stress_scenarios.jsonl`

Latest pointers are copied to:

- `Logs/train_runs/_latest/stress_report_latest.json`
- `Logs/train_runs/_latest/stress_scenarios_latest.jsonl`

These artifacts include scenario multipliers, key metrics (return, drawdown, turnover), and pass/fail status.

## Promotion gate

Promotion decisions are fail-closed if stress artifacts are missing or failing. The gate requires:

- Baseline pass
- Stress scenarios within risk limits

If stress artifacts are missing, promotion is rejected with structured reasons so the UI can surface the evidence paths.
