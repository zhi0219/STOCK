from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from tools.train_service import (
    RUNS_ROOT,
    SERVICE_ROOT,
    STATE_PATH as DEFAULT_STATE_PATH,
    _kill_switch_paths,
    _load_kill_switch_cfg,
)
from tools.wakeup_dashboard import (
    MISSING_FIELD_TEXT,
    SummaryParseResult,
    find_latest_summary_md,
    parse_summary_key_fields,
)


@dataclass
class TrainingHudSnapshot:
    mode: str
    mode_detail: str
    kill_switch: str
    kill_switch_paths: List[str]
    data_health: str
    data_health_detail: str
    stage: str
    run_id: str
    elapsed: str
    next_iteration: str
    budgets: Dict[str, str]
    risk: Dict[str, str]
    equity: str
    summary_path: Path | None
    run_dir: Path | None
    state_path: Path
    rolling_summary: Path


def _read_state(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
    except Exception:
        return None


def _human_elapsed(start: datetime | None, now: datetime) -> str:
    if not start:
        return "unknown"
    delta = now - start
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _estimate_next_iteration(state: Dict[str, object], now: datetime) -> str:
    eta = _parse_dt(state.get("next_iteration_eta"))
    if eta:
        remaining = (eta - now).total_seconds()
        if remaining > 0:
            return f"{int(remaining)}s"
        return "due"
    cooldown = None
    config = state.get("config", {}) if isinstance(state.get("config", {}), dict) else {}
    try:
        cooldown = int(config.get("cooldown_seconds_between_episodes", 0))
    except Exception:
        cooldown = None
    last_end = _parse_dt(state.get("last_episode_end_ts"))
    if cooldown and last_end:
        diff = (last_end + timedelta(seconds=cooldown)) - now
        if diff.total_seconds() > 0:
            return f"{int(diff.total_seconds())}s"
    return "unknown"


def _resolve_summary_path(state: Dict[str, object]) -> Path | None:
    summary_text = state.get("last_summary_path") if isinstance(state, dict) else None
    if summary_text:
        path = Path(str(summary_text))
        if path.exists():
            return path
    _, summary = find_latest_summary_md(RUNS_ROOT)
    return summary


def _summarize_risk(summary: SummaryParseResult | None) -> Dict[str, str]:
    if not summary:
        return {
            "max_drawdown": MISSING_FIELD_TEXT,
            "turnover": MISSING_FIELD_TEXT,
            "reject_count": MISSING_FIELD_TEXT,
            "gates_triggered": MISSING_FIELD_TEXT,
            "rejects": MISSING_FIELD_TEXT,
        }
    rejects = ", ".join(summary.reject_reasons_top3[:3]) if summary.reject_reasons_top3 else MISSING_FIELD_TEXT
    return {
        "max_drawdown": summary.max_drawdown,
        "turnover": summary.turnover,
        "reject_count": summary.reject_count,
        "gates_triggered": summary.gates_triggered,
        "rejects": rejects or MISSING_FIELD_TEXT,
    }


def _budget_view(state: Dict[str, object]) -> Dict[str, str]:
    config = state.get("config", {}) if isinstance(state.get("config", {}), dict) else {}
    return {
        "episodes_completed": str(state.get("episodes_completed", 0)),
        "max_per_hour": str(config.get("max_episodes_per_hour", "?")),
        "max_per_day": str(config.get("max_episodes_per_day", "?")),
        "disk_budget_mb": str(config.get("max_total_train_runs_mb", "?")),
    }


def _mode_from_state(state: Dict[str, object], kill_paths: List[Path], now: datetime) -> tuple[str, str]:
    triggered = [str(p) for p in kill_paths if p.exists()]
    if triggered:
        return "SAFE", ", ".join(triggered)
    stop_reason = state.get("stop_reason") if isinstance(state, dict) else None
    heartbeat = _parse_dt(state.get("last_heartbeat_ts"))
    fresh = heartbeat and (now - heartbeat) < timedelta(seconds=180)
    if stop_reason:
        return "STOPPED", str(stop_reason)
    if fresh:
        return "RUNNING", "heartbeat ok"
    if state:
        return "OBSERVE", "heartbeat stale"
    return "STOPPED", "state missing"


def compute_training_hud(state_path: Path = DEFAULT_STATE_PATH, summary_path: Path | None = None) -> TrainingHudSnapshot:
    now = datetime.now(timezone.utc)
    state = _read_state(state_path)
    cfg = _load_kill_switch_cfg()
    kill_paths = _kill_switch_paths(cfg)
    mode, mode_detail = _mode_from_state(state, kill_paths, now)

    summary_candidate = summary_path or _resolve_summary_path(state)
    summary_result = parse_summary_key_fields(summary_candidate) if summary_candidate else None

    run_dir = None
    if isinstance(state, dict) and state.get("last_run_dir"):
        run_dir = Path(str(state.get("last_run_dir")))

    elapsed = _human_elapsed(_parse_dt(state.get("service_start_ts")), now) if state else "unknown"

    data_health_detail = ""
    last_error = state.get("last_error") if isinstance(state, dict) else None
    if last_error:
        data_health = "ISSUE"
        data_health_detail = str(last_error)
    elif mode == "RUNNING":
        data_health = "OK"
    else:
        data_health = "UNKNOWN"
    if summary_result and summary_result.warning:
        data_health_detail = data_health_detail or summary_result.warning
        if data_health == "OK":
            data_health = "WARN"

    stage_text = "booting"
    if state.get("stop_reason"):
        stage_text = str(state.get("stop_reason"))
    elif state.get("last_episode_end_ts"):
        stage_text = "cooldown/loop"
    elif state.get("last_episode_start_ts"):
        stage_text = "episode_running"

    return TrainingHudSnapshot(
        mode=mode,
        mode_detail=mode_detail,
        kill_switch="TRIPPED" if [p for p in kill_paths if p.exists()] else "CLEAR",
        kill_switch_paths=[str(p) for p in kill_paths],
        data_health=data_health,
        data_health_detail=data_health_detail or "",
        stage=stage_text,
        run_id=str(run_dir) if run_dir else "(none)",
        elapsed=elapsed,
        next_iteration=_estimate_next_iteration(state, now),
        budgets=_budget_view(state),
        risk=_summarize_risk(summary_result),
        equity=summary_result.net_change if summary_result else MISSING_FIELD_TEXT,
        summary_path=summary_candidate,
        run_dir=run_dir,
        state_path=state_path,
        rolling_summary=SERVICE_ROOT / "rolling_summary.md",
    )


__all__ = ["TrainingHudSnapshot", "compute_training_hud"]
