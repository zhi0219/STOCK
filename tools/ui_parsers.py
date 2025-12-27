from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _require_fields(payload: dict[str, Any], required: Iterable[str]) -> list[str]:
    return [key for key in required if key not in payload]


def load_progress_judge_latest(path: Path, fallback: Path | None = None) -> dict[str, Any]:
    payload = _safe_read_json(path)
    if not payload and fallback is not None:
        payload = _safe_read_json(fallback)
    if not payload:
        return {
            "status": "missing",
            "missing_reason": "progress_judge_latest_missing",
            "recommendation": "INSUFFICIENT_DATA",
            "scores": {"vs_do_nothing": None, "vs_buy_hold": None},
            "drivers": [],
            "not_improving_reasons": ["progress_judge_latest_missing"],
            "suggested_next_actions": ["Run SIM training to generate judge artifacts."],
            "trend": {"direction": "unknown", "window": 0, "values": []},
            "risk_metrics": {},
        }
    missing = _require_fields(payload, ["schema_version", "created_utc", "run_id"])
    if missing:
        payload["missing_reason"] = f"progress_judge_missing_fields:{','.join(missing)}"
    payload.setdefault("recommendation", "INSUFFICIENT_DATA")
    payload.setdefault("scores", {"vs_do_nothing": None, "vs_buy_hold": None})
    payload.setdefault("drivers", [])
    payload.setdefault("not_improving_reasons", [])
    payload.setdefault("suggested_next_actions", [])
    payload.setdefault("trend", {"direction": "unknown", "window": 0, "values": []})
    payload.setdefault("risk_metrics", {})
    return payload


def load_policy_history_latest(path: Path) -> dict[str, Any]:
    payload = _safe_read_json(path)
    if not payload:
        return {"status": "missing", "missing_reason": "policy_history_latest_missing"}
    missing = _require_fields(payload, ["schema_version", "created_utc", "run_id", "policy_version"])
    if missing:
        payload["missing_reason"] = f"policy_history_missing_fields:{','.join(missing)}"
    return payload


def load_engine_status(
    tournament_path: Path,
    decision_path: Path,
    judge_path: Path,
) -> dict[str, Any]:
    def _load_status(path: Path, missing_reason: str) -> dict[str, Any]:
        payload = _safe_read_json(path)
        if not payload:
            return {"status": "missing", "missing_reason": missing_reason}
        missing = _require_fields(payload, ["schema_version", "created_utc", "run_id"])
        if missing:
            payload["missing_reason"] = f"{missing_reason}:{','.join(missing)}"
        payload.setdefault("status", "ok")
        return payload

    return {
        "tournament": _load_status(tournament_path, "tournament_latest_missing"),
        "promotion": _load_status(decision_path, "promotion_decision_latest_missing"),
        "judge": _load_status(judge_path, "progress_judge_latest_missing"),
    }


def load_policy_history(registry_path: Path, events_path: Path | None = None) -> list[dict[str, Any]]:
    registry = _safe_read_json(registry_path)
    history = registry.get("history", []) if isinstance(registry.get("history"), list) else []
    entries: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "ts_utc": item.get("ts_utc", ""),
                "policy_version": item.get("policy_version", ""),
                "decision": item.get("action", ""),
                "reason": item.get("evidence", ""),
                "evidence": item.get("evidence", ""),
            }
        )

    if events_path and events_path.exists():
        try:
            for line in events_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if not isinstance(event, dict):
                    continue
                if str(event.get("event_type")) != "GUARD_PROPOSAL":
                    continue
                entries.append(
                    {
                        "ts_utc": event.get("ts_utc", ""),
                        "policy_version": event.get("policy_version", ""),
                        "decision": "CANDIDATE",
                        "reason": event.get("message", ""),
                        "evidence": event.get("event_id", ""),
                    }
                )
        except Exception:
            return entries

    entries.sort(key=lambda row: str(row.get("ts_utc", "")), reverse=True)
    return entries
