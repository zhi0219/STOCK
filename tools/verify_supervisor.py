from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "Data"
LOGS_DIR = ROOT / "Logs"
STATE_PATH = LOGS_DIR / "supervisor_state.json"
KILL_SWITCH = DATA_DIR / "KILL_SWITCH"
SUPERVISOR = ROOT / "tools" / "supervisor.py"
DUMMY = ROOT / "tools" / "dummy_source.py"


def is_process_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
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
    except Exception:
        return False
    return True


def run_supervisor(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def clean_files() -> None:
    for path in [KILL_SWITCH, STATE_PATH]:
        path.unlink(missing_ok=True)
    for hb in LOGS_DIR.glob("_tmp_dummy_*.txt"):
        hb.unlink(missing_ok=True)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    with STATE_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def wait_for_heartbeat(name: str, timeout: float = 5.0) -> Path | None:
    path = LOGS_DIR / f"_tmp_dummy_{name}.txt"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return path
        time.sleep(0.2)
    return None


def verify_start(quotes_name: str, alerts_name: str) -> tuple[int, int]:
    start_cmd = [
        sys.executable,
        str(SUPERVISOR),
        "start",
        "--quotes-script",
        str(DUMMY),
        "--alerts-script",
        str(DUMMY),
    ]
    proc = run_supervisor(start_cmd)
    expect(proc.returncode == 0, f"start failed: {proc.stderr} {proc.stdout}")
    expect(STATE_PATH.exists(), "state file missing after start")
    state = read_state()
    quotes_pid = state.get("sources", {}).get("quotes", {}).get("pid")
    alerts_pid = state.get("sources", {}).get("alerts", {}).get("pid")
    expect(is_process_running(quotes_pid), "quotes not running")
    expect(is_process_running(alerts_pid), "alerts not running")
    expect(wait_for_heartbeat(quotes_name), "quotes heartbeat missing")
    expect(wait_for_heartbeat(alerts_name), "alerts heartbeat missing")
    return int(quotes_pid), int(alerts_pid)


def verify_already_running(quotes_pid: int, alerts_pid: int) -> None:
    proc = run_supervisor([sys.executable, str(SUPERVISOR), "start"])
    expect(proc.returncode == 0, "second start should not fail")
    state = read_state()
    new_q = state.get("sources", {}).get("quotes", {}).get("pid")
    new_a = state.get("sources", {}).get("alerts", {}).get("pid")
    expect(new_q == quotes_pid, "quotes pid changed on double start")
    expect(new_a == alerts_pid, "alerts pid changed on double start")


def verify_stop() -> None:
    proc = run_supervisor([sys.executable, str(SUPERVISOR), "stop", "--timeout", "10"])
    expect(proc.returncode == 0, f"stop failed: {proc.stderr} {proc.stdout}")
    expect(KILL_SWITCH.exists(), "kill switch not created")
    state = read_state()
    sources = state.get("sources", {})
    for entry in sources.values():
        pid = entry.get("pid")
        expect(not is_process_running(pid), f"process {pid} still running")
        expect(entry.get("running") is False, "state running flag not false")


def main() -> int:
    # dummy_source uses the default name "source" for both processes
    quotes_name = "source"
    alerts_name = "source"
    clean_files()
    try:
        q_pid, a_pid = verify_start(quotes_name, alerts_name)
        verify_already_running(q_pid, a_pid)
        verify_stop()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    finally:
        clean_files()
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
