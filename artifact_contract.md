# PR19 Artifact Contract

This document defines the minimal JSON fields required for SIM-only training artifacts consumed by the UI and gates.

## Required base fields (all JSON artifacts)

Every PR19 artifact **must** include the following fields:

- `schema_version`
- `created_utc`
- `run_id`
- `policy_version` (when the artifact is tied to a policy run)

## Artifact list (PR19)

### Candidate selection (`candidates.json`)
- Required: base fields, `seed`, `pool_manifest`, `baselines`, `candidates`

### Tournament scoring (`tournament.json`)
- Required: base fields, `seed`, `entries`

### Promotion recommendation (`promotion_recommendation.json`)
- Required: base fields, `candidate_id`, `recommendation`, `reasons`, `metrics`

### Promotion decision (`promotion_decision.json`)
- Required: base fields, `ts_utc`, `candidate_id`, `decision`, `reasons`, `required_next_steps`

### Progress judge (`progress_judge_latest.json`)
- Required: base fields, `recommendation`, `scores`, `trend`, `risk_metrics`

### Policy history snapshot (`policy_history_latest.json`)
- Required: base fields, `last_decision`

## Latest pointer locations (stable for UI)

These paths are updated atomically after each training iteration:

- `Logs/train_runs/_latest/candidates_latest.json`
- `Logs/train_runs/_latest/tournament_latest.json`
- `Logs/train_runs/_latest/promotion_decision_latest.json`
- `Logs/train_runs/_latest/policy_history_latest.json`
- `Logs/train_runs/_latest/progress_judge_latest.json`

## Notes
- Artifacts are SIM-only and do **not** trigger live trading.
- Missing fields should be treated as **fail-closed** by the UI and gates.
