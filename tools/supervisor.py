from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "Data"
LOGS_DIR = ROOT / "Logs"
STATE_PATH = LOGS_DIR / "supervisor_state.json"
KILL_SWITCH = DATA_DIR / "KILL_SWITCH"
DEFAULT_QUOTE_SCRIPT = ROOT / "quotes.py"
DEFAULT_ALERT_SCRIPT = ROOT / "alerts.py"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def et_now() -> datetime:
    # Eastern time without third-party tz data; this is sufficient for logging.
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.isoformat()


def write_state(state: Dict) -> None:
    ensure_dirs()
    tmp = STATE_PATH.with_suffix(".tmp")
    with tempfile.NamedTemporaryFile("w", delete=False, dir=STATE_PATH.parent, encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        tmp_path = Path(fh.name)
    tmp_path.replace(STATE_PATH)


def read_state() -> Dict:
    if not STATE_PATH.exists():
        return {}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def is_process_running(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/fi", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            return str(pid) in result.stdout
        proc_path = Path(f"/proc/{pid}")
        if proc_path.exists():
            status_path = proc_path / "status"
            if status_path.exists():
                try:
                    text = status_path.read_text(encoding="utf-8")
                    if "State:\tZ" in text:
                        return False
                except Exception:
                    pass
            return True
        os.kill(pid, 0)
    except FileNotFoundError:
        return False
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def build_state_entry(cmd: str, pid: int, started: datetime) -> Dict:
    return {
        "pid": pid,
        "started_utc": isoformat(started),
        "cmd": cmd,
        "running": True,
    }


def render_state(quotes_proc: Optional[subprocess.Popen], alerts_proc: Optional[subprocess.Popen]) -> Dict:
    now_utc = utc_now()
    now_et = et_now()
    quotes_state = {}
    alerts_state = {}
    if quotes_proc:
        quotes_state = build_state_entry("quotes", quotes_proc.pid, now_utc)
    if alerts_proc:
        alerts_state = build_state_entry("alerts", alerts_proc.pid, now_utc)
    return {
        "schema_version": 1,
        "ts_utc": isoformat(now_utc),
        "ts_et": isoformat(now_et),
        "sources": {
            "quotes": quotes_state,
            "alerts": alerts_state,
        },
        "last_action": "start",
    }


def describe_state(state: Dict) -> str:
    sources = state.get("sources", {})
    status_bits = []
    for name in ("quotes", "alerts"):
        entry = sources.get(name) or {}
        pid = entry.get("pid")
        running = entry.get("running")
        status_bits.append(f"{name}: pid={pid} running={running}")
    return "; ".join(status_bits)


def update_running_flags(state: Dict) -> Dict:
    sources = state.get("sources", {})
    for name, entry in sources.items():
        pid = entry.get("pid")
        entry["running"] = bool(is_process_running(pid))
    state["ts_utc"] = isoformat(utc_now())
    state["ts_et"] = isoformat(et_now())
    return state


def start_sources(args: argparse.Namespace) -> int:
    ensure_dirs()
    if KILL_SWITCH.exists():
        if not args.force_remove_kill_switch:
            print("[WARN] KILL_SWITCH present. Remove or pass --force-remove-kill-switch to continue.")
            return 1
        KILL_SWITCH.unlink(missing_ok=True)

    existing_state = read_state()
    existing_state = update_running_flags(existing_state) if existing_state else existing_state
    sources = existing_state.get("sources", {}) if existing_state else {}
    running_now = [name for name, entry in sources.items() if entry.get("running")]
    if running_now:
        print("already running: " + ", ".join(running_now))
        write_state(existing_state)
        return 0

    log_path = LOGS_DIR / "supervisor.log"
    log_file = log_path.open("a", encoding="utf-8")
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    quotes_cmd = [sys.executable, str(args.quotes_script)]
    alerts_cmd = [sys.executable, str(args.alerts_script)]
    quotes_proc = subprocess.Popen(
        quotes_cmd,
        cwd=ROOT,
        stdout=log_file,
        stderr=log_file,
        creationflags=creationflags,
    )
    alerts_proc = subprocess.Popen(
        alerts_cmd,
        cwd=ROOT,
        stdout=log_file,
        stderr=log_file,
        creationflags=creationflags,
    )
    log_file.close()
    state = render_state(quotes_proc, alerts_proc)
    write_state(state)
    print("started", describe_state(state))
    return 0


def stop_sources(args: argparse.Namespace) -> int:
    ensure_dirs()
    KILL_SWITCH.touch()
    state = read_state()
    sources = state.get("sources", {}) if state else {}
    timeout = args.timeout
    deadline = time.time() + timeout
    running = True
    while time.time() < deadline:
        running = False
        for entry in sources.values():
            pid = entry.get("pid")
            if is_process_running(pid):
                running = True
                break
        if not running:
            break
        time.sleep(0.5)

    state["last_action"] = "stop"
    update_running_flags(state)
    write_state(state)
    if running:
        print("[WARN] some sources may still be running. Check processes manually.")
        return 1
    print("stopped")
    return 0


def status_sources(_: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        print("no state file found")
        return 1
    state = update_running_flags(state)
    write_state(state)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def restart_sources(args: argparse.Namespace) -> int:
    stop_sources(args)
    return start_sources(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervisor for quotes and alerts")
    sub = parser.add_subparsers(dest="command", required=True)

    start_p = sub.add_parser("start", help="start quotes and alerts")
    start_p.add_argument("--quotes-script", type=Path, default=DEFAULT_QUOTE_SCRIPT)
    start_p.add_argument("--alerts-script", type=Path, default=DEFAULT_ALERT_SCRIPT)
    start_p.add_argument("--force-remove-kill-switch", action="store_true")
    start_p.set_defaults(func=start_sources)

    stop_p = sub.add_parser("stop", help="stop quotes and alerts")
    stop_p.add_argument("--timeout", type=float, default=10.0)
    stop_p.set_defaults(func=stop_sources)

    status_p = sub.add_parser("status", help="show status")
    status_p.set_defaults(func=status_sources)

    restart_p = sub.add_parser("restart", help="restart quotes and alerts")
    restart_p.add_argument("--quotes-script", type=Path, default=DEFAULT_QUOTE_SCRIPT)
    restart_p.add_argument("--alerts-script", type=Path, default=DEFAULT_ALERT_SCRIPT)
    restart_p.add_argument("--force-remove-kill-switch", action="store_true")
    restart_p.add_argument("--timeout", type=float, default=10.0)
    restart_p.set_defaults(func=restart_sources)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
