from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from tools.paths import policy_registry_runtime_path, to_repo_relative
from tools.replay_artifacts import REPLAY_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
PROGRESS_JUDGE_DIR = RUNS_ROOT / "progress_judge"
XP_SNAPSHOT_DIR = RUNS_ROOT / "progress_xp"
XP_SNAPSHOT_LATEST = XP_SNAPSHOT_DIR / "xp_snapshot_latest.json"


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


def _select_latest_by_mtime(candidates: Iterable[Path]) -> Path | None:
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def _load_latest_with_fallback(
    latest_path: Path,
    fallback_candidates: Iterable[Path],
    missing_reason: str,
    required_fields: Iterable[str],
    next_actions: list[str],
) -> dict[str, Any]:
    fallback_list = list(fallback_candidates)
    payload = _safe_read_json(latest_path)
    source_mode = "latest_pointer"
    source_path = to_repo_relative(latest_path)
    if not payload:
        fallback = _select_latest_by_mtime(fallback_list)
        if fallback:
            payload = _safe_read_json(fallback)
            source_mode = "fallback_scan"
            source_path = to_repo_relative(fallback)
    if not payload:
        return {
            "status": "missing",
            "missing_reason": missing_reason,
            "missing_artifacts": [latest_path.name],
            "searched_paths": [to_repo_relative(latest_path)] + [to_repo_relative(p) for p in fallback_list],
            "suggested_next_actions": next_actions,
            "source": {"mode": "missing", "path": to_repo_relative(latest_path)},
        }
    missing = _require_fields(payload, required_fields)
    if missing:
        payload["missing_reason"] = f"{missing_reason}:{','.join(missing)}"
    payload.setdefault("source", {"mode": source_mode, "path": source_path})
    return payload


def load_progress_judge_latest(path: Path, fallback: Path | None = None) -> dict[str, Any]:
    candidates = list(PROGRESS_JUDGE_DIR.glob("progress_judge_*.json"))
    if fallback is not None:
        candidates.append(fallback)
    payload = _load_latest_with_fallback(
        path,
        candidates,
        "progress_judge_latest_missing",
        ["schema_version", "created_utc", "run_id"],
        ["Run SIM training to generate judge artifacts."],
    )
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
    source_mode = "latest_pointer"
    source_path = to_repo_relative(path)
    registry_path = policy_registry_runtime_path()
    if not payload:
        registry = _safe_read_json(registry_path)
        history = registry.get("history", []) if isinstance(registry.get("history"), list) else []
        last_entry = history[-1] if history else {}
        if isinstance(last_entry, dict) and last_entry:
            payload = {
                "schema_version": 1,
                "created_utc": last_entry.get("ts_utc") or "",
                "run_id": last_entry.get("run_id") or "",
                "policy_version": last_entry.get("policy_version") or "",
                "last_decision": {
                    "ts_utc": last_entry.get("ts_utc"),
                    "decision": last_entry.get("action"),
                    "candidate_id": last_entry.get("candidate_id"),
                    "reasons": last_entry.get("reasons"),
                },
                "registry_last_entry": last_entry,
                "history_tail": history[-5:],
                "fallback_registry": to_repo_relative(registry_path),
            }
            source_mode = "fallback_registry"
            source_path = to_repo_relative(registry_path)
    if not payload:
        return {
            "status": "missing",
            "missing_reason": "policy_history_latest_missing",
            "missing_artifacts": [path.name],
            "searched_paths": [to_repo_relative(path), to_repo_relative(registry_path)],
            "suggested_next_actions": ["Run SIM training to generate policy history artifacts."],
            "source": {"mode": "missing", "path": to_repo_relative(path)},
        }
    missing = _require_fields(payload, ["schema_version", "created_utc", "run_id", "policy_version"])
    if missing:
        payload["missing_reason"] = f"policy_history_missing_fields:{','.join(missing)}"
    payload.setdefault("source", {"mode": source_mode, "path": source_path})
    return payload


def load_engine_status(
    tournament_path: Path,
    decision_path: Path,
    judge_path: Path,
) -> dict[str, Any]:
    tournament_candidates = RUNS_ROOT.glob("**/tournament.json")
    promotion_candidates = RUNS_ROOT.glob("**/promotion_decision.json")
    judge_candidates = list(PROGRESS_JUDGE_DIR.glob("progress_judge_*.json"))
    if judge_path.exists():
        judge_candidates.append(judge_path)

    def _load_status(path: Path, missing_reason: str, candidates: Iterable[Path]) -> dict[str, Any]:
        payload = _load_latest_with_fallback(
            path,
            candidates,
            missing_reason,
            ["schema_version", "created_utc", "run_id"],
            ["Run SIM training to generate engine artifacts."],
        )
        payload.setdefault("status", "ok")
        return payload

    return {
        "tournament": _load_status(tournament_path, "tournament_latest_missing", tournament_candidates),
        "promotion": _load_status(decision_path, "promotion_decision_latest_missing", promotion_candidates),
        "judge": _load_status(judge_path, "progress_judge_latest_missing", judge_candidates),
    }


def load_pr28_latest(
    tournament_path: Path,
    judge_path: Path,
    promotion_path: Path,
    history_path: Path,
) -> dict[str, Any]:
    tournament_candidates = RUNS_ROOT.glob("**/tournament_result.json")
    judge_candidates = RUNS_ROOT.glob("**/judge_result.json")
    promotion_candidates = RUNS_ROOT.glob("**/promotion_decision.json")

    def _load_pr28(path: Path, missing_reason: str, candidates: Iterable[Path]) -> dict[str, Any]:
        payload = _load_latest_with_fallback(
            path,
            candidates,
            missing_reason,
            ["schema_version", "ts_utc", "run_id", "git_commit"],
            ["Run PR28 SIM training to generate artifacts."],
        )
        payload.setdefault("status", "ok")
        return payload

    history_payload = _safe_read_json(history_path)
    if not history_payload:
        history_payload = {
            "status": "missing",
            "missing_reason": "promotion_history_latest_missing",
            "missing_artifacts": [history_path.name],
            "searched_paths": [str(history_path)],
            "suggested_next_actions": ["Run PR28 SIM training to generate promotion history."],
            "source": {"mode": "missing", "path": str(history_path)},
        }
    else:
        missing = _require_fields(history_payload, ["schema_version", "ts_utc", "run_id", "git_commit"])
        if missing:
            history_payload["missing_reason"] = f"promotion_history_missing_fields:{','.join(missing)}"
        history_payload.setdefault("source", {"mode": "latest_pointer", "path": str(history_path)})

    return {
        "tournament": _load_pr28(tournament_path, "tournament_result_latest_missing", tournament_candidates),
        "judge": _load_pr28(judge_path, "judge_result_latest_missing", judge_candidates),
        "promotion": _load_pr28(promotion_path, "promotion_decision_latest_missing", promotion_candidates),
        "history": history_payload,
    }


def load_xp_snapshot_latest(path: Path = XP_SNAPSHOT_LATEST) -> dict[str, Any]:
    candidates = list(XP_SNAPSHOT_DIR.glob("xp_snapshot_*.json"))
    payload = _load_latest_with_fallback(
        path,
        candidates,
        "xp_snapshot_latest_missing",
        ["xp_spec_version", "xp_total", "level"],
        ["Run python -m tools.write_xp_snapshot to generate XP artifacts."],
    )
    payload.setdefault("status", "INSUFFICIENT_DATA")
    payload.setdefault("xp_total", 0)
    payload.setdefault("level", 1)
    payload.setdefault("level_progress", 0.0)
    payload.setdefault("missing_reasons", [])
    payload.setdefault("xp_breakdown", [])
    return payload


def load_replay_index_latest() -> dict[str, Any]:
    candidates = [path for path in RUNS_ROOT.glob("**/replay/replay_index.json") if path.is_file()]
    latest = _select_latest_by_mtime(candidates)
    if latest is None:
        return {
            "status": "missing",
            "missing_reason": "replay_index_missing",
            "missing_artifacts": ["replay_index.json"],
            "searched_paths": [to_repo_relative(path) for path in candidates],
            "suggested_next_actions": ["Run PR33 SIM training to generate replay artifacts."],
            "source": {"mode": "missing", "path": "Logs/train_runs/**/replay/replay_index.json"},
        }
    payload = _safe_read_json(latest)
    if not payload:
        return {
            "status": "missing",
            "missing_reason": "replay_index_unreadable",
            "missing_artifacts": [latest.name],
            "searched_paths": [to_repo_relative(latest)],
            "suggested_next_actions": ["Re-run PR33 SIM training to regenerate replay artifacts."],
            "source": {"mode": "missing", "path": to_repo_relative(latest)},
        }
    payload.setdefault("source", {"mode": "latest_by_mtime", "path": to_repo_relative(latest)})
    if payload.get("schema_version") != REPLAY_SCHEMA_VERSION:
        payload["missing_reason"] = "replay_index_schema_mismatch"
    return payload


def load_decision_cards(path: Path, max_rows: int = 5000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
            if len(rows) >= max_rows:
                break
    except Exception:
        return []
    return rows


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
