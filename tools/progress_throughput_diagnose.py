from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
STATE_PATH = LOGS_DIR / "train_service" / "state.json"
PROGRESS_INDEX_PATH = RUNS_ROOT / "progress_index.json"
LATEST_DIR = RUNS_ROOT / "_latest"
SERVICE_KILL_SWITCH = LOGS_DIR / "train_service" / "KILL_SWITCH"
GLOBAL_KILL_SWITCH = ROOT / "Data" / "KILL_SWITCH"

LATEST_POINTER_NAMES = {
    "progress_judge": "progress_judge_latest.json",
    "promotion_decision": "promotion_decision_latest.json",
    "tournament": "tournament_latest.json",
    "candidates": "candidates_latest.json",
    "policy_history": "policy_history_latest.json",
}


@dataclass
class ThroughputDiagnosis:
    status: str
    primary_reason: str
    details: str
    evidence: Dict[str, object]


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
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


def _kill_switch_paths() -> List[Path]:
    return [SERVICE_KILL_SWITCH, GLOBAL_KILL_SWITCH]


def _latest_pointer_missing(latest_dir: Path) -> List[str]:
    missing = []
    for name, filename in LATEST_POINTER_NAMES.items():
        path = latest_dir / filename
        if not path.exists():
            missing.append(name)
    return missing


def _progress_runs(entries: object) -> List[dict]:
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _runs_last_hour(entries: List[dict], now: datetime) -> int:
    one_hour_ago = now - timedelta(hours=1)
    count = 0
    for entry in entries:
        ts = _parse_dt(entry.get("mtime"))
        if ts and ts >= one_hour_ago:
            count += 1
    return count


def compute_throughput_diagnosis(
    state_path: Path = STATE_PATH,
    progress_index_path: Path = PROGRESS_INDEX_PATH,
    latest_dir: Path = LATEST_DIR,
    now: datetime | None = None,
) -> ThroughputDiagnosis:
    now = now or datetime.now(timezone.utc)
    state = _read_json(state_path)
    progress_index = _read_json(progress_index_path)
    entries = _progress_runs(progress_index.get("entries"))

    evidence: Dict[str, object] = {
        "state_path": str(state_path),
        "progress_index_path": str(progress_index_path),
        "runs_found": len(entries),
    }

    reasons: List[str] = []

    kill_paths = _kill_switch_paths()
    triggered = [str(path) for path in kill_paths if path.exists()]
    if triggered:
        reasons.append("kill_switch_present")
        evidence["kill_switch_paths"] = triggered

    heartbeat = _parse_dt(state.get("last_heartbeat_ts")) if state else None
    heartbeat_age = None
    if heartbeat:
        heartbeat_age = int((now - heartbeat).total_seconds())
    evidence["heartbeat_age_s"] = heartbeat_age if heartbeat_age is not None else ""

    if not state:
        reasons.append("state_missing")
    elif heartbeat_age is None or heartbeat_age > 180:
        reasons.append("service_heartbeat_stale")

    stop_reason = state.get("stop_reason") if isinstance(state, dict) else None
    if stop_reason:
        evidence["stop_reason"] = stop_reason

    last_error = state.get("last_error") if isinstance(state, dict) else None
    if last_error:
        reasons.append("data_health_unhealthy")
        evidence["last_error"] = last_error

    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    max_per_hour = config.get("max_episodes_per_hour")
    max_per_day = config.get("max_episodes_per_day")
    max_trades = config.get("max_trades")
    episode_seconds = config.get("episode_seconds")
    if any(
        isinstance(value, int) and value <= 2 for value in [max_per_hour, max_per_day]
    ) or (isinstance(max_trades, int) and max_trades < 50):
        reasons.append("budgets_too_tight")
    if isinstance(episode_seconds, int) and episode_seconds <= 30:
        reasons.append("budgets_too_tight")

    if entries:
        last_run_dir = entries[0].get("run_dir")
    else:
        last_run_dir = state.get("last_run_dir") if isinstance(state, dict) else None
    if last_run_dir and not Path(str(last_run_dir)).exists():
        reasons.append("retention_archived")

    missing_latest = _latest_pointer_missing(latest_dir)
    if missing_latest:
        reasons.append("latest_pointers_missing")
        evidence["missing_latest"] = ",".join(missing_latest)

    if not progress_index_path.exists():
        reasons.append("progress_index_missing")

    runs_last_hour = _runs_last_hour(entries, now)
    evidence["runs_last_hour"] = runs_last_hour

    if not reasons:
        primary_reason = "ok"
        status = "OK"
        details = "Throughput looks healthy; artifacts and heartbeat are present."
    else:
        priority = [
            "kill_switch_present",
            "service_heartbeat_stale",
            "state_missing",
            "data_health_unhealthy",
            "budgets_too_tight",
            "retention_archived",
            "latest_pointers_missing",
            "progress_index_missing",
        ]
        for key in priority:
            if key in reasons:
                primary_reason = key
                break
        else:
            primary_reason = reasons[0]
        status = "FAIL" if primary_reason in {"kill_switch_present", "service_heartbeat_stale"} else "WARN"
        details = f"Issues detected: {', '.join(sorted(set(reasons)))}"

    return ThroughputDiagnosis(status=status, primary_reason=primary_reason, details=details, evidence=evidence)


def _render_summary(diag: ThroughputDiagnosis) -> str:
    return "|".join(
        [
            "THROUGHPUT_DIAG_SUMMARY",
            f"status={diag.status}",
            f"primary_reason={diag.primary_reason}",
            f"details={diag.details}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose training throughput (SIM-only)")
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    parser.add_argument("--progress-index-path", type=Path, default=PROGRESS_INDEX_PATH)
    parser.add_argument("--latest-dir", type=Path, default=LATEST_DIR)
    args = parser.parse_args()

    diag = compute_throughput_diagnosis(
        state_path=args.state_path,
        progress_index_path=args.progress_index_path,
        latest_dir=args.latest_dir,
    )

    summary = _render_summary(diag)
    print("THROUGHPUT_DIAG_START")
    print(summary)
    print(json.dumps({"evidence": diag.evidence}, ensure_ascii=False))
    print("THROUGHPUT_DIAG_END")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
