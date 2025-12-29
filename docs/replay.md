# PR33 Replay (SIM-only)

## Scope and safety

- **SIM-only / READ_ONLY**: replay artifacts never place orders or connect to brokers.
- Deterministic only: decision cards are derived from deterministic scores, guards, and metrics.
- Fail-closed: missing or unreadable replay artifacts must be treated as unavailable.

## Artifact layout

Per run, replay outputs live under `Logs/train_runs/<run_id>/`:

- `replay/replay_index.json`
- `replay/decision_cards.jsonl`
- `replay/replay_events.jsonl`
- `_latest/replay_index_latest.json`
- `_latest/decision_cards_latest.jsonl`

In CI gate mode (PR33), the latest files are copied to:

- `artifacts/Logs/train_runs/_pr33_gate/_latest/replay_index_latest.json`
- `artifacts/Logs/train_runs/_pr33_gate/_latest/decision_cards_latest.jsonl`

All paths written into artifacts are repo-relative and use forward slashes.

## Replay index schema

`replay_index.json` is a single JSON object with:

- `schema_version`
- `created_ts_utc`
- `run_id`
- `git_commit`
- `runner`
- `counts`: `{num_cards, num_events}`
- `truncation`: `{truncated, max_cards, max_bytes, dropped_cards}`
- `pointers`: repo-relative paths to JSONL files

## Decision card schema

Each line in `decision_cards.jsonl` is one JSON object:

Required fields:

- `ts_utc` (string)
- `step_id` (int or string)
- `episode_id` (string)
- `symbol` (string or `"n/a"`)
- `action` (`BUY`/`SELL`/`HOLD`/`REJECT`/`NOOP`)
- `size` (number)
- `price_snapshot` (`{last, currency?}`)
- `signals` (`[{name, value, unit?}]`)
- `guards` (`{kill_switch, data_health, cooldown_ok, limits_ok, no_lookahead_ok, walk_forward_window_id}`)
- `decision` (`{accepted, reject_reason_codes}`)
- `evidence` (`{paths, hashes?}`)

Optional fields:

- `pnl_delta`
- `equity`
- `drawdown`

## Bounds & truncation

Replay output is bounded:

- `MAX_DECISION_CARDS = 2000`
- `MAX_DECISION_CARDS_BYTES = 2MB`

If bounds are exceeded, truncation is deterministic (latest cards kept) and recorded in the replay index.

## Deterministic guarantees

- No LLM reasoning is included in cards or UI.
- All values are derived from deterministic metrics, guards, and gate results.
- Evidence paths are repo-relative for cross-platform replay.
