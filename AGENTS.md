# PROJECT CONSTITUTION

## Non-negotiable guardrails
- READ_ONLY forever: never place trades, send orders, modify funds, log in to brokers, or attempt to bypass any verification/2FA/captcha/region restrictions.
- Keep automation advisory-only: no instructions or hints that resemble buy/sell/target-price/position-sizing actions.

## Reporting/output discipline
- Separate **Facts**, **Analysis**, and **Hypotheses/Predictions**. Any conclusion must cite evidence from `events_*`/`events.jsonl` or `status.json` (e.g., `[evidence: events_20240101.jsonl#L42]` or `[evidence: event_id=abc123 ts_utc=...]`).
- No trading recommendations; all guidance must stay observational.
- Prefer deterministic, reproducible commands; use `pathlib` for filesystem paths.

## Pull request template (copy/paste)
```
Summary
- 

Risks
- 

Testing
- 
```

## Windows virtualenv commands
- Use `..\ .venv\Scripts\python.exe` style (e.g., `.\.venv\Scripts\python.exe .\tools\verify_smoke.py`).
- Do not require PowerShell-specific knowledge; commands must be copy/paste friendly.
