from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Deque, Dict, List, Tuple

import yaml

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sim_autopilot import _kill_switch_enabled, _kill_switch_path


ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = ROOT / "Logs" / "train_runs"
SERVICE_ROOT = ROOT / "Logs" / "train_service"
STATE_PATH = SERVICE_ROOT / "state.json"
ROLLING_SUMMARY_PATH = SERVICE_ROOT / "rolling_summary.md"
SERVICE_KILL_SWITCH = SERVICE_ROOT / "KILL_SWITCH"
TRAIN_DAEMON = ROOT / "tools" / "train_daemon.py"

CADENCE_PRESETS: Dict[str, Dict[str, int]] = {
    "micro": {
        "episode_seconds": 120,
        "cooldown_seconds_between_episodes": 5,
        "max_episodes_per_hour": 24,
        "max_episodes_per_day": 200,
        "max_steps": 1500,
        "max_trades": 200,
        "max_events_per_hour": 400,
        "max_disk_mb": 2500,
        "max_runtime_per_day": 6 * 3600,
    },
    "normal": {
        "episode_seconds": 300,
        "cooldown_seconds_between_episodes": 10,
        "max_episodes_per_hour": 12,
        "max_episodes_per_day": 120,
        "max_steps": 5000,
        "max_trades": 500,
        "max_events_per_hour": 300,
        "max_disk_mb": 5000,
        "max_runtime_per_day": 8 * 3600,
    },
    "conservative": {
        "episode_seconds": 600,
        "cooldown_seconds_between_episodes": 20,
        "max_episodes_per_hour": 6,
        "max_episodes_per_day": 60,
        "max_steps": 4000,
        "max_trades": 200,
        "max_events_per_hour": 200,
        "max_disk_mb": 3000,
        "max_runtime_per_day": 4 * 3600,
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utf8_env() -> dict:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load_kill_switch_cfg() -> dict:
    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _kill_switch_paths(cfg: dict | None = None) -> List[Path]:
    cfg = cfg or _load_kill_switch_cfg()
    paths = [SERVICE_KILL_SWITCH]
    if _kill_switch_enabled(cfg):
        paths.append(_kill_switch_path(cfg).expanduser().resolve())
    return paths


def _kill_switch_triggered(cfg: dict | None = None) -> Tuple[bool, str]:
    for path in _kill_switch_paths(cfg):
        if path.exists():
            return True, str(path)
    return False, ""


def _parse_daemon_markers(text: str) -> Dict[str, str]:
    markers: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        markers[key.strip()] = value.strip()
    return markers


def _append_rolling_summary(entry: str) -> None:
    ROLLING_SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ROLLING_SUMMARY_PATH.exists():
        header = "# Rolling Training Summary\n\n"
        ROLLING_SUMMARY_PATH.write_text(header, encoding="utf-8")
    with ROLLING_SUMMARY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(entry.rstrip() + "\n")


def _sleep_with_heartbeat(
    seconds: float, episodes_completed: int, last_run_dir: str | None, state: Dict[str, object]
) -> None:
    deadline = time.monotonic() + max(seconds, 0)
    state["next_iteration_eta"] = (_now() + timedelta(seconds=max(seconds, 0))).isoformat()
    state["next_run_eta_s"] = int(max(seconds, 0))
    _write_state(state)
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        time.sleep(min(remaining, 5))
        state["last_heartbeat_ts"] = _now().isoformat()
        state["next_iteration_eta"] = (_now() + timedelta(seconds=max(remaining, 0))).isoformat()
        state["next_run_eta_s"] = int(max(remaining, 0))
        _write_state(state)
        print(
            f"SERVICE_HEARTBEAT|episodes_completed={episodes_completed}|last_run_dir={last_run_dir or ''}",
            flush=True,
        )
        cfg = _load_kill_switch_cfg()
        tripped, reason = _kill_switch_triggered(cfg)
        if tripped:
            raise SystemExit(f"SERVICE_STOP|reason=kill_switch|path={reason}")


def _write_state(state: Dict[str, object]) -> None:
    _atomic_write_json(STATE_PATH, state)


def _enforce_runs_root(path: Path) -> Path:
    allowed = RUNS_ROOT.resolve()
    candidate = path.expanduser().resolve()
    try:
        candidate.relative_to(allowed)
    except ValueError:
        if candidate != allowed:
            raise ValueError(f"runs_root must be within {allowed}; got {candidate}")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _compute_wait_time(history: Deque[datetime], args: argparse.Namespace) -> float:
    now = _now()
    one_hour_ago = now - timedelta(hours=1)
    one_day_ago = now - timedelta(days=1)

    while history and history[0] < one_day_ago:
        history.popleft()

    hourly = [ts for ts in history if ts >= one_hour_ago]
    if args.max_episodes_per_day and len(history) >= args.max_episodes_per_day:
        return -1
    if args.max_episodes_per_hour and len(hourly) >= args.max_episodes_per_hour:
        next_slot = hourly[0] + timedelta(hours=1)
        return max(0.0, (next_slot - now).total_seconds())
    return 0.0


def _runs_per_hour(history: Deque[datetime], now: datetime | None = None) -> int:
    now = now or _now()
    one_hour_ago = now - timedelta(hours=1)
    return sum(1 for ts in history if ts >= one_hour_ago)


def _run_episode(
    idx: int, args: argparse.Namespace, cfg: dict, state: Dict[str, object]
) -> Tuple[str | None, str | None, str]:
    planned_seconds = int(args.episode_seconds)
    print(f"EPISODE_START|i={idx}|planned_seconds={planned_seconds}", flush=True)
    state["last_episode_start_ts"] = _now().isoformat()
    state["last_planned_seconds"] = planned_seconds
    state["last_run_duration_s"] = None
    state["next_run_eta_s"] = 0
    _write_state(state)
    start_time = time.monotonic()
    cmd = [
        sys.executable,
        str(TRAIN_DAEMON),
        "--max-runtime-seconds",
        str(planned_seconds),
        "--max-steps",
        str(args.max_steps),
        "--max-trades",
        str(args.max_trades),
        "--max-events-per-hour",
        str(args.max_events_per_hour),
        "--max-disk-mb",
        str(args.max_disk_mb),
        "--max-runtime-per-day",
        str(args.max_runtime_per_day),
        "--retain-days",
        str(args.retain_days),
        "--retain-latest-n",
        str(args.retain_latest_n),
        "--max-total-train-runs-mb",
        str(args.max_total_train_runs_mb),
        "--runs-root",
        str(args.runs_root),
    ]
    if args.input:
        cmd.extend(["--input", str(args.input)])

    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
    )

    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    markers = _parse_daemon_markers(proc.stdout or "")
    run_dir = markers.get("RUN_DIR")
    summary_path = markers.get("SUMMARY_PATH")
    stop_reason = markers.get("STOP_REASON") or "episode_failed"
    if proc.returncode != 0 and stop_reason == "episode_failed":
        stop_reason = f"return_code_{proc.returncode}"

    state["last_episode_end_ts"] = _now().isoformat()
    state["last_run_dir"] = run_dir
    state["last_summary_path"] = summary_path
    if proc.returncode != 0:
        state["last_error"] = proc.stderr or proc.stdout or "episode failed"
    else:
        state["last_error"] = None
    state["last_run_duration_s"] = int(max(0.0, time.monotonic() - start_time))
    _write_state(state)

    return run_dir, summary_path, stop_reason


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SIM-only 24/7 training service",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--cadence-preset",
        choices=sorted(CADENCE_PRESETS.keys()),
        default="micro",
        dest="cadence_preset",
        help="Cadence preset for throughput/safety (micro recommended for frequent SIM updates)",
    )
    parser.add_argument("--episode-seconds", type=int, default=None, dest="episode_seconds")
    parser.add_argument("--max-episodes-per-hour", type=int, default=None, dest="max_episodes_per_hour")
    parser.add_argument("--max-episodes-per-day", type=int, default=None, dest="max_episodes_per_day")
    parser.add_argument(
        "--cooldown-seconds-between-episodes",
        type=int,
        default=None,
        dest="cooldown_seconds_between_episodes",
    )
    parser.add_argument("--max-steps", type=int, default=None, dest="max_steps")
    parser.add_argument("--max-trades", type=int, default=None, dest="max_trades")
    parser.add_argument("--max-events-per-hour", type=int, default=None, dest="max_events_per_hour")
    parser.add_argument("--max-disk-mb", type=int, default=None, dest="max_disk_mb")
    parser.add_argument("--max-runtime-per-day", type=int, default=None, dest="max_runtime_per_day")
    parser.add_argument("--retain-days", type=int, default=7, dest="retain_days")
    parser.add_argument("--retain-latest-n", type=int, default=50, dest="retain_latest_n")
    parser.add_argument(
        "--max-total-train-runs-mb", type=int, default=5000, dest="max_total_train_runs_mb"
    )
    parser.add_argument("--input", type=str, default="", help="Quotes CSV for episodes")
    parser.add_argument(
        "--runs-root",
        type=str,
        default=str(RUNS_ROOT),
        help="Root for train_daemon runs (must live under Logs/train_runs)",
    )
    return parser.parse_args(argv or sys.argv[1:])


def _apply_cadence_preset(args: argparse.Namespace) -> None:
    preset = CADENCE_PRESETS.get(args.cadence_preset, CADENCE_PRESETS["micro"])
    for key, value in preset.items():
        if getattr(args, key, None) in (None, ""):
            setattr(args, key, value)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    _apply_cadence_preset(args)
    cfg = _load_kill_switch_cfg()
    runs_root = _enforce_runs_root(Path(args.runs_root))
    args.runs_root = runs_root
    args.input = Path(args.input) if args.input else None
    if args.input and not args.input.exists():
        print(f"SERVICE_STOP|reason=input_missing|path={args.input}")
        return 1

    SERVICE_ROOT.mkdir(parents=True, exist_ok=True)

    service_state: Dict[str, object] = {
        "service_start_ts": _now().isoformat(),
        "last_episode_end_ts": None,
        "last_episode_start_ts": None,
        "episodes_completed": 0,
        "last_error": None,
        "last_run_dir": None,
        "last_summary_path": None,
        "service_pid": os.getpid(),
        "last_heartbeat_ts": _now().isoformat(),
        "stop_reason": None,
        "cadence_preset": args.cadence_preset,
        "target_runs_per_hour": int(args.max_episodes_per_hour),
        "computed_runs_per_hour": 0,
        "last_run_duration_s": None,
        "next_run_eta_s": None,
        "config": {
            "episode_seconds": args.episode_seconds,
            "cooldown_seconds_between_episodes": args.cooldown_seconds_between_episodes,
            "max_episodes_per_hour": args.max_episodes_per_hour,
            "max_episodes_per_day": args.max_episodes_per_day,
            "max_steps": args.max_steps,
            "max_trades": args.max_trades,
            "max_events_per_hour": args.max_events_per_hour,
            "max_disk_mb": args.max_disk_mb,
            "max_runtime_per_day": args.max_runtime_per_day,
            "max_total_train_runs_mb": args.max_total_train_runs_mb,
            "retain_days": args.retain_days,
            "retain_latest_n": args.retain_latest_n,
            "runs_root": str(args.runs_root),
        },
    }
    _write_state(service_state)
    print("SERVICE_START", flush=True)

    history: Deque[datetime] = deque()
    episode_idx = 1

    while True:
        now = _now()
        service_state["last_heartbeat_ts"] = now.isoformat()
        service_state["computed_runs_per_hour"] = _runs_per_hour(history, now)
        _write_state(service_state)

        tripped, reason = _kill_switch_triggered(cfg)
        if tripped:
            service_state["stop_reason"] = f"kill_switch:{reason}"
            _write_state(service_state)
            print(f"SERVICE_STOP|reason=kill_switch|path={reason}", flush=True)
            return 0

        wait_time = _compute_wait_time(history, args)
        if wait_time < 0:
            service_state["stop_reason"] = "max_episodes_per_day"
            _write_state(service_state)
            print("SERVICE_STOP|reason=max_episodes_per_day", flush=True)
            return 0
        if wait_time > 0:
            service_state["next_iteration_eta"] = (_now() + timedelta(seconds=wait_time)).isoformat()
            service_state["next_run_eta_s"] = int(max(wait_time, 0))
            _write_state(service_state)
            print(
                f"SERVICE_HEARTBEAT|episodes_completed={service_state['episodes_completed']}|last_run_dir={service_state.get('last_run_dir') or ''}",
                flush=True,
            )
            try:
                _sleep_with_heartbeat(wait_time, int(service_state["episodes_completed"]), service_state.get("last_run_dir"), service_state)
            except SystemExit as exc:
                service_state["stop_reason"] = "kill_switch"
                _write_state(service_state)
                print(str(exc), flush=True)
                return 0
            continue

        try:
            run_dir, summary_path, stop_reason = _run_episode(episode_idx, args, cfg, service_state)
        except SystemExit as exc:
            service_state["stop_reason"] = "kill_switch"
            _write_state(service_state)
            print(str(exc), flush=True)
            return 0
        except Exception as exc:  # pragma: no cover - defensive
            service_state["last_error"] = str(exc)
            service_state["stop_reason"] = "exception"
            _write_state(service_state)
            print(f"SERVICE_STOP|reason=exception|detail={exc}")
            return 1

        history.append(_now())
        service_state["episodes_completed"] = int(service_state.get("episodes_completed", 0)) + 1
        _write_state(service_state)

        _append_rolling_summary(
            f"- {service_state['last_episode_end_ts']} | episode={episode_idx} | stop_reason={stop_reason} | run_dir={run_dir} | summary={summary_path}"
        )

        print(
            f"EPISODE_END|i={episode_idx}|run_dir={run_dir}|summary_path={summary_path}|stop_reason={stop_reason}",
            flush=True,
        )
        episode_idx += 1

        try:
            service_state["next_iteration_eta"] = (_now() + timedelta(seconds=float(args.cooldown_seconds_between_episodes))).isoformat()
            _write_state(service_state)
            _sleep_with_heartbeat(
                float(args.cooldown_seconds_between_episodes),
                int(service_state["episodes_completed"]),
                service_state.get("last_run_dir"),
                service_state,
            )
        except SystemExit as exc:
            service_state["stop_reason"] = "kill_switch"
            _write_state(service_state)
            print(str(exc), flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
