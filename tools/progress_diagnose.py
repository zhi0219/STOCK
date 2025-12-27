from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
STATE_PATH = LOGS_DIR / "train_service" / "state.json"
PROGRESS_INDEX_PATH = RUNS_ROOT / "progress_index.json"
SERVICE_KILL_SWITCH = LOGS_DIR / "train_service" / "KILL_SWITCH"
GLOBAL_KILL_SWITCH = ROOT / "Data" / "KILL_SWITCH"

REASON_PRIORITY = [
    "kill_switch_tripped",
    "service_heartbeat_stale",
    "budget_exhausted",
    "data_health_unhealthy",
    "cooldown_backoff_waiting",
    "artifacts_missing",
    "unknown",
]


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
    except Exception:
        return None


def _read_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_progress_index(path: Path) -> Dict[str, object]:
    payload = _read_json(path)
    return payload if payload else {}


def _heartbeat_age_seconds(state: Dict[str, object], now: datetime) -> int | None:
    heartbeat = _parse_dt(state.get("last_heartbeat_ts"))
    if not heartbeat:
        return None
    return int((now - heartbeat).total_seconds())


def _next_run_eta_seconds(state: Dict[str, object], now: datetime) -> int | None:
    eta = _parse_dt(state.get("next_iteration_eta"))
    if not eta:
        return None
    return int((eta - now).total_seconds())


def _budget_stop_reason(stop_reason: str | None) -> bool:
    if not stop_reason:
        return False
    lowered = stop_reason.lower()
    return lowered.startswith("max_") or "budget" in lowered or "episodes_per_day" in lowered


def _collect_missing_runs(entries: List[dict]) -> List[str]:
    missing = []
    for entry in entries:
        if entry.get("missing_reason"):
            missing.append(str(entry.get("run_id", "unknown")))
    return missing


def _rank_reasons(reasons: List[str]) -> List[str]:
    unique = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return sorted(unique, key=lambda item: REASON_PRIORITY.index(item))


def _diagnosis_summary(
    primary_reason: str,
    evidence: Dict[str, object],
    runs_found: int,
) -> str:
    tail = ""
    if runs_found <= 2:
        tail = f" Only {runs_found} runs found; check stop_reason={evidence.get('stop_reason')} and next_run_in={evidence.get('next_run_in_s')}s."
    if primary_reason == "kill_switch_tripped":
        return f"Kill switch is tripped ({evidence.get('kill_switch_paths')}).{tail}"
    if primary_reason == "service_heartbeat_stale":
        heartbeat_age = evidence.get("heartbeat_age_s")
        return f"Service heartbeat is stale (heartbeat_age_s={heartbeat_age}).{tail}"
    if primary_reason == "budget_exhausted":
        return f"Service stopped due to budget/limit (stop_reason={evidence.get('stop_reason')}).{tail}"
    if primary_reason == "data_health_unhealthy":
        return f"Service reports data health issues (last_error={evidence.get('last_error')}).{tail}"
    if primary_reason == "cooldown_backoff_waiting":
        return f"Cooldown/backoff active (next_run_in={evidence.get('next_run_in_s')}s).{tail}"
    if primary_reason == "artifacts_missing":
        return f"Run artifacts are missing or incomplete; progress index cannot fully summarize runs.{tail}"
    summary = "Service appears healthy; progress should advance as runs complete."
    return summary + tail


def compute_progress_diagnosis(
    state_path: Path = STATE_PATH,
    progress_index_path: Path = PROGRESS_INDEX_PATH,
    kill_switch_paths: List[Path] | None = None,
    now: datetime | None = None,
) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    state = _read_json(state_path)
    progress_index = _load_progress_index(progress_index_path)
    entries = progress_index.get("entries", []) if isinstance(progress_index.get("entries", []), list) else []
    runs_found = len(entries)
    missing_run_ids = _collect_missing_runs(entries)

    kill_paths = kill_switch_paths or [SERVICE_KILL_SWITCH, GLOBAL_KILL_SWITCH]
    kill_switch_tripped = any(path.exists() for path in kill_paths)
    stop_reason = state.get("stop_reason") if isinstance(state, dict) else None
    if isinstance(stop_reason, str) and "kill_switch" in stop_reason:
        kill_switch_tripped = True

    heartbeat_age = _heartbeat_age_seconds(state, now)
    heartbeat_stale = False
    if not state:
        heartbeat_stale = True
    elif heartbeat_age is None or heartbeat_age > 180:
        heartbeat_stale = True

    budget_exhausted = _budget_stop_reason(str(stop_reason)) if stop_reason else False
    last_error = state.get("last_error") if isinstance(state, dict) else None
    data_health_unhealthy = bool(last_error)

    next_run_in = _next_run_eta_seconds(state, now)
    cooldown_waiting = next_run_in is not None and next_run_in > 0

    artifacts_missing = False
    if not progress_index_path.exists():
        artifacts_missing = True
    elif runs_found == 0:
        artifacts_missing = True
    elif missing_run_ids:
        artifacts_missing = True

    reasons: List[str] = []
    if kill_switch_tripped:
        reasons.append("kill_switch_tripped")
    if heartbeat_stale:
        reasons.append("service_heartbeat_stale")
    if budget_exhausted:
        reasons.append("budget_exhausted")
    if data_health_unhealthy:
        reasons.append("data_health_unhealthy")
    if cooldown_waiting:
        reasons.append("cooldown_backoff_waiting")
    if artifacts_missing:
        reasons.append("artifacts_missing")
    if not reasons:
        reasons.append("unknown")

    reasons_ranked = _rank_reasons(reasons)
    primary_reason = reasons_ranked[0]
    evidence = {
        "runs_found": runs_found,
        "missing_runs": missing_run_ids,
        "stop_reason": stop_reason or "",
        "last_error": last_error or "",
        "heartbeat_age_s": heartbeat_age if heartbeat_age is not None else "",
        "next_run_in_s": next_run_in if next_run_in is not None else "",
        "kill_switch_paths": [str(path) for path in kill_paths],
        "state_path": str(state_path),
        "progress_index_path": str(progress_index_path),
    }

    summary = _diagnosis_summary(primary_reason, evidence, runs_found)
    status = "OK" if primary_reason == "unknown" else "WARN"

    return {
        "primary_reason": primary_reason,
        "reasons_ranked": reasons_ranked,
        "evidence": evidence,
        "summary": summary,
        "status": status,
    }


def main() -> int:
    diagnosis = compute_progress_diagnosis()
    summary = "|".join(
        [
            "PROGRESS_DIAG_SUMMARY",
            f"status={diagnosis.get('status')}",
            f"reason={diagnosis.get('primary_reason')}",
        ]
    )
    print("PROGRESS_DIAG_START")
    print(summary)
    print(json.dumps(diagnosis, ensure_ascii=False))
    print("PROGRESS_DIAG_END")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
