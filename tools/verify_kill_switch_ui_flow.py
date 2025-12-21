from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parent.parent
SUPERVISOR = ROOT / "tools" / "supervisor.py"
DATA_DIR = ROOT / "Data"
KILL_SWITCH = DATA_DIR / "KILL_SWITCH"


def run_start(extra_args: Tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SUPERVISOR), "start", *extra_args]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def main() -> int:
    backup_path = None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if KILL_SWITCH.exists():
            backup_path = KILL_SWITCH.with_suffix(".bak.verify")
            try:
                backup_path.unlink()
            except FileNotFoundError:
                pass
            KILL_SWITCH.rename(backup_path)

        KILL_SWITCH.write_text("TEST", encoding="utf-8")

        failures = []

        first = run_start()
        output_blob = (first.stdout or "") + (first.stderr or "")
        if first.returncode == 0:
            failures.append("start without --force should fail when KILL_SWITCH is present")
        if "KILL_SWITCH" not in output_blob:
            failures.append("warning about KILL_SWITCH should be present in output")

        second = run_start(("--force-remove-kill-switch",))
        if second.returncode != 0:
            failures.append("start with --force-remove-kill-switch should succeed")
        if KILL_SWITCH.exists():
            failures.append("KILL_SWITCH should be removed after force start")

        for msg in failures:
            print(f"FAIL: {msg}")

        if failures:
            return 1

        print("PASS: kill switch UI flow verified")
        return 0
    finally:
        if backup_path and backup_path.exists():
            backup_path.rename(KILL_SWITCH)
        elif KILL_SWITCH.exists():
            KILL_SWITCH.unlink()


if __name__ == "__main__":
    sys.exit(main())
