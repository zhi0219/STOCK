from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "Data"
LOGS_DIR = ROOT / "Logs"
SUMMARY = {
    "interpreter": sys.executable,
    "versions": "pandas=?, yaml=?, yfinance=?",
    "passed": 0,
}


def fail(msg: str, extra_output: str | None = None) -> None:
    print(f"FAIL: {msg}")
    if extra_output:
        print("--- subprocess output (stdout+stderr) ---")
        print(extra_output.rstrip())
    print(f"Interpreter: {SUMMARY['interpreter']}")
    print(f"Dependencies: {SUMMARY['versions']}")
    print(
        "FAIL: smoke failed; copy the messages above (including stdout/stderr) when seeking help."
    )
    sys.exit(1)


def record_pass(label: str) -> None:
    SUMMARY["passed"] += 1
    print(f"[OK] {label}")


def check_imports() -> None:
    try:
        import pandas as pd  # noqa: F401
        import yaml  # noqa: F401
        import yfinance  # noqa: F401
    except ImportError as e:  # pragma: no cover - environment guard
        fail(
            (
                "Missing dependency: {}. Install via PowerShell: "
                ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            ).format(e.name or "package")
        )
    SUMMARY["versions"] = (
        f"pandas={pd.__version__}, yaml={getattr(yaml, '__version__', 'unknown')}, "
        f"yfinance={yfinance.__version__}"
    )
    print(f"Python executable: {SUMMARY['interpreter']}")
    print(f"Dependencies: {SUMMARY['versions']}")
    record_pass("imports available")


def ensure_paths() -> None:
    required = [
        ROOT / "config.yaml",
        ROOT / "alerts.py",
        ROOT / "quotes.py",
        ROOT / "tools" / "inject_quote.py",
        ROOT / "tools" / "verify_cooldown.py",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        fail(f"Missing required files: {', '.join(missing)}")

    for path in (DATA_DIR, LOGS_DIR):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            print(f"created {path}")
    record_pass("required files and folders ready")


def compile_targets() -> None:
    targets = [
        ROOT / "main.py",
        ROOT / "quotes.py",
        ROOT / "alerts.py",
        ROOT / "tools" / "inject_quote.py",
        ROOT / "tools" / "verify_cooldown.py",
        ROOT / "tools" / "verify_smoke.py",
    ]
    tail_events = ROOT / "tools" / "tail_events.py"
    if tail_events.exists():
        targets.append(tail_events)

    for target in targets:
        try:
            py_compile.compile(str(target), doraise=True)
        except Exception as e:  # pragma: no cover - compile failures are direct FAIL
            fail(f"Compile failed for {target}: {e}")
    record_pass("py_compile succeeded")


def _tail_lines(path: Path, count: int = 3) -> Iterable[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return []
    except Exception:
        return []
    lines = text.splitlines()
    return lines[-count:]


def run_with_kill_switch(
    script: Path, banner: str, *, banner_log: Path | None = None
) -> None:
    kill_path = DATA_DIR / "KILL_SWITCH"
    if kill_path.exists():
        fail(
            f"Kill switch already present at {kill_path}; remove it then rerun smoke test."
        )

    kill_path.touch()
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT))

    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except subprocess.TimeoutExpired as e:  # pragma: no cover - runtime guard
        kill_path.unlink(missing_ok=True)
        fail(f"{script.name} timed out with kill switch present", extra_output=str(e))

    kill_path.unlink(missing_ok=True)

    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        fail(
            f"{script.name} exited with code {completed.returncode} while kill switch present",
            extra_output=output,
        )

    banner_seen = banner in output
    if not banner_seen and banner_log and banner_log.exists():
        banner_seen = any(banner in line for line in _tail_lines(banner_log, 5))
    if not banner_seen:
        fail(
            f"Missing startup banner '{banner}' from {script.name}",
            extra_output=output,
        )

    if "KILL_SWITCH detected" not in output:
        fail(
            f"Kill switch message not seen in {script.name} output",
            extra_output=output,
        )
    record_pass(f"{script.name} honored kill switch (exit 0)")


def check_logs() -> None:
    status_path = LOGS_DIR / "status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if not ("ts_utc" in status or "ts_et" in status):
                fail("Logs/status.json missing ts_utc/ts_et core fields")
            print("status.json core fields ok")
        except Exception as e:
            fail(f"Failed to parse {status_path}: {e}")
    else:
        print("status.json not found (skipped)")

    event_candidates = sorted(LOGS_DIR.glob("events*.jsonl"))
    if event_candidates:
        latest = event_candidates[-1]
        print(f"checking {latest.name} tail")
        bad_lines = 0
        parsed = 0
        for line in _tail_lines(latest, 3):
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
                parsed += 1
            except Exception:
                bad_lines += 1
        if bad_lines:
            print(f"[WARN] skipped {bad_lines} bad line(s) in {latest.name}")
        if parsed:
            print(f"parsed {parsed} recent event line(s)")
    else:
        print("no events file found (skipped)")

    tail_events_path = ROOT / "tools" / "tail_events.py"
    if tail_events_path.exists():
        result = subprocess.run(
            [sys.executable, str(tail_events_path), "--limit", "1"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            fail(
                "tools/tail_events.py --limit 1 failed",
                extra_output=(result.stdout or "") + (result.stderr or ""),
            )
        print("tail_events.py --limit 1 completed")
    record_pass("logs/status/events sanity")


def main() -> None:
    check_imports()
    ensure_paths()
    compile_targets()
    run_with_kill_switch(ROOT / "alerts.py", "ALERTS_START")
    run_with_kill_switch(
        ROOT / "quotes.py", "QUOTES_START", banner_log=LOGS_DIR / "run.log"
    )
    check_logs()

    print(f"Interpreter: {SUMMARY['interpreter']}")
    print(f"Dependencies: {SUMMARY['versions']}")
    print(
        f"PASS: smoke verified ({SUMMARY['passed']} checks passed)."
        " Next step: share this output if you need assistance."
    )


if __name__ == "__main__":
    main()
