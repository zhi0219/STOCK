from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.paths import repo_root, runtime_dir, to_repo_relative

DEFAULT_BUDGET = {
    "schema_version": 1,
    "max_trades_per_day": 12,
    "min_seconds_between_trades": 300,
    "max_turnover_per_day": 15000,
    "max_cost_per_trade": None,
    "volatility_scale": {"enabled": False, "multiplier": 1.0},
}


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _merge_budget(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key == "volatility_scale" and isinstance(value, dict):
            existing = merged.get("volatility_scale")
            if isinstance(existing, dict):
                combined = dict(existing)
                combined.update(value)
                merged["volatility_scale"] = combined
            else:
                merged["volatility_scale"] = dict(value)
        else:
            merged[key] = value
    return merged


def load_overtrading_budget(
    seed_path: Path | None = None,
    runtime_path: Path | None = None,
) -> dict[str, Any]:
    root = repo_root()
    seed_path = seed_path or (root / "Data" / "overtrading_budget.json")
    runtime_path = runtime_path or (runtime_dir() / "overtrading_budget.json")

    status = "PASS"
    missing: list[str] = []
    sources: dict[str, str | None] = {
        "seed": to_repo_relative(seed_path),
        "runtime": to_repo_relative(runtime_path),
    }

    seed_payload = _safe_read_json(seed_path)
    if seed_payload is None:
        status = "MISSING"
        missing.append("seed_missing_or_invalid")
        seed_payload = DEFAULT_BUDGET

    runtime_payload = _safe_read_json(runtime_path)
    if runtime_payload is None:
        missing.append("runtime_missing_or_invalid")
        runtime_payload = {}

    budget = _merge_budget(seed_payload, runtime_payload)

    return {
        "status": status,
        "budget": budget,
        "missing_reasons": missing,
        "sources": sources,
    }


__all__ = ["load_overtrading_budget", "DEFAULT_BUDGET"]
