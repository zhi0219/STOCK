from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
LATEST_DIR = RUNS_ROOT / "_latest"
ARTIFACTS_DIR = ROOT / "artifacts"


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def load_overtrading_calibration(latest_path: Path | None = None) -> dict[str, Any] | None:
    latest_path = latest_path or (LATEST_DIR / "overtrading_calibration_latest.json")
    payload = _safe_read_json(latest_path)
    if payload:
        payload.setdefault("source", {"mode": "latest_pointer", "path": to_repo_relative(latest_path)})
    return payload


def select_overtrading_budget(
    base_budget: dict[str, Any],
    calibration: dict[str, Any] | None,
    regime_label: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    budget = dict(base_budget)
    calibration_info: dict[str, Any] = {
        "status": "MISSING",
        "regime_label": regime_label,
        "sample_size": None,
        "calibration_path": None,
        "latest_path": None,
        "insufficient_reasons": ["calibration_missing"],
    }
    if not calibration:
        return budget, calibration_info

    source = calibration.get("source", {}) if isinstance(calibration.get("source"), dict) else {}
    latest_path = source.get("path") if isinstance(source.get("path"), str) else None
    paths = calibration.get("paths", {}) if isinstance(calibration.get("paths"), dict) else {}
    artifacts_path = paths.get("artifacts") if isinstance(paths.get("artifacts"), str) else None
    status = str(calibration.get("status") or "UNKNOWN")
    created_utc = calibration.get("created_utc")
    created_dt = _parse_ts(created_utc)
    freshness_hours = None
    if created_dt:
        freshness_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600.0
    min_samples_per_regime = calibration.get("min_samples_per_regime")

    regimes = calibration.get("regimes", {}) if isinstance(calibration.get("regimes"), dict) else {}
    regime_payload = regimes.get(regime_label) if isinstance(regime_label, str) else None
    if not isinstance(regime_payload, dict):
        regime_payload = None

    calibration_info = {
        "status": status if regime_payload else "MISSING",
        "regime_label": regime_label,
        "sample_size": regime_payload.get("sample_size") if regime_payload else None,
        "calibration_path": artifacts_path or to_repo_relative(ARTIFACTS_DIR / "overtrading_calibration.json"),
        "latest_path": latest_path,
        "insufficient_reasons": [],
        "created_utc": created_utc,
        "freshness_hours": round(freshness_hours, 2) if isinstance(freshness_hours, (int, float)) else None,
        "min_samples_per_regime": min_samples_per_regime,
    }

    if not regime_payload:
        calibration_info["insufficient_reasons"].append("regime_missing")
        return budget, calibration_info

    if regime_payload.get("insufficient_data"):
        calibration_info["status"] = "INSUFFICIENT_DATA"
        calibration_info["insufficient_reasons"].extend(regime_payload.get("insufficient_reasons", []))
        return budget, calibration_info

    recommended = regime_payload.get("recommended_budget", {}) if isinstance(regime_payload.get("recommended_budget"), dict) else {}
    for key, value in recommended.items():
        if value is not None:
            budget[key] = value

    calibration_info["status"] = "OK"
    calibration_info["insufficient_reasons"] = []
    return budget, calibration_info


__all__ = ["load_overtrading_calibration", "select_overtrading_budget"]
