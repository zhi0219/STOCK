from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_progress_judge_latest(path: Path) -> dict[str, Any]:
    payload = _safe_read_json(path)
    if not payload:
        return {
            "status": "missing",
            "recommendation": "INSUFFICIENT_DATA",
            "scores": {"vs_do_nothing": None, "vs_buy_hold": None},
            "drivers": [],
            "not_improving_reasons": ["progress_judge_latest_missing"],
            "suggested_next_actions": ["Run SIM training to generate judge artifacts."],
            "trend": {"direction": "unknown", "window": 0, "values": []},
            "risk_metrics": {},
        }
    payload.setdefault("recommendation", "INSUFFICIENT_DATA")
    payload.setdefault("scores", {"vs_do_nothing": None, "vs_buy_hold": None})
    payload.setdefault("drivers", [])
    payload.setdefault("not_improving_reasons", [])
    payload.setdefault("suggested_next_actions", [])
    payload.setdefault("trend", {"direction": "unknown", "window": 0, "values": []})
    payload.setdefault("risk_metrics", {})
    return payload


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
