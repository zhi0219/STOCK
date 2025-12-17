# CODE AUDIT

## Commands run
- `python -m py_compile ./main.py ./quotes.py ./alerts.py`
- `python main.py` (fails without `pyyaml` installed)

## Findings
- Runtime dependency `pyyaml` is not installed in the environment; `python main.py` exits with `ModuleNotFoundError`. Install allowed dependency `pyyaml` before running entrypoints.
- `alerts.py` ignored top-level `flat_repeats` and `stale_seconds` settings in `config.yaml`, always falling back to defaults.

## Changes made
- `alerts.py` now honors both `alerts` section overrides and the top-level `flat_repeats` / `stale_seconds` settings, matching the provided `config.yaml` defaults.
