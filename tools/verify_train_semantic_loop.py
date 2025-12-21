"""Verifier for semantic training loop (SIM-only)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAIN_DAEMON = ROOT / "tools" / "train_daemon.py"
NO_LOOKAHEAD = ROOT / "tools" / "verify_no_lookahead_sim.py"
EVENTS_PATH = ROOT / "Logs" / "events_train.jsonl"
STATE_PATH = ROOT / "Logs" / "train_daemon_state.json"
DEFAULT_KILL = ROOT / "Data" / "KILL_SWITCH"


def _write_quotes(path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = []
    for offset in range(0, 120, 10):
        rows.append(
            {
                "ts_utc": (now + timedelta(seconds=offset)).isoformat(),
                "symbol": "MSFT",
                "price": str(200 + ((offset // 10) % 3) - 1),
                "source": "synthetic",
            }
        )
    headers = list(rows[0].keys())
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _run_once(quotes_path: Path, runs_root: Path) -> None:
    cmd = [
        sys.executable,
        str(TRAIN_DAEMON),
        "--input",
        str(quotes_path),
        "--runs-root",
        str(runs_root),
        "--max-runtime-seconds",
        "10",
        "--max-steps",
        "5",
        "--max-trades",
        "5",
        "--max-events-per-hour",
        "50",
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise SystemExit(proc.returncode)


def _assert_events() -> None:
    if not EVENTS_PATH.exists():
        raise AssertionError("events_train.jsonl missing")
    types = []
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        types.append(event.get("event_type"))
    required = {
        "TOURNAMENT_DONE",
        "GUARD_PROPOSAL",
        "POLICY_CANDIDATE_CREATED",
        "PROMOTION_DECISION",
    }
    missing = [et for et in sorted(required) if et not in types]
    if missing:
        raise AssertionError(f"Missing events: {', '.join(missing)}")


def _assert_state() -> None:
    if not STATE_PATH.exists():
        raise AssertionError("train_daemon_state.json missing")
    payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    for key in ("run_id", "stage", "last_success_ts", "last_report_path", "policy_version", "degraded_flags", "stop_reason"):
        if key not in payload:
            raise AssertionError(f"state missing {key}")


def _assert_kill_switch(quotes_path: Path, runs_root: Path) -> None:
    DEFAULT_KILL.unlink(missing_ok=True)
    cmd = [
        sys.executable,
        str(TRAIN_DAEMON),
        "--input",
        str(quotes_path),
        "--runs-root",
        str(runs_root),
        "--max-runtime-seconds",
        "30",
        "--max-steps",
        "50",
        "--max-trades",
        "5",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    DEFAULT_KILL.write_text("KILL", encoding="utf-8")
    deadline = time.time() + 5.0
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    if proc.poll() is None:
        proc.terminate()
    stdout, stderr = proc.communicate(timeout=5)
    if proc.returncode not in {0, 1}:
        print(stdout)
        print(stderr)
        raise AssertionError(f"unexpected return: {proc.returncode}")
    if not EVENTS_PATH.exists():
        raise AssertionError("events missing after kill switch")
    found = False
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("event_type") == "TRAIN_STOPPED_KILL_SWITCH":
            found = True
    if not found:
        raise AssertionError("TRAIN_STOPPED_KILL_SWITCH not emitted")
    DEFAULT_KILL.unlink(missing_ok=True)


def _assert_no_lookahead() -> None:
    proc = subprocess.run(
        [sys.executable, str(NO_LOOKAHEAD)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise SystemExit(proc.returncode)


def main() -> None:
    status = "PASS"
    reason = "ok"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            runs_root = ROOT / "Logs" / "train_runs" / "_semantic"
            quotes_path = base / "quotes.csv"
            _write_quotes(quotes_path)
            EVENTS_PATH.unlink(missing_ok=True)
            STATE_PATH.unlink(missing_ok=True)
            DEFAULT_KILL.unlink(missing_ok=True)

            _run_once(quotes_path, runs_root)
            _assert_events()
            _assert_state()
            _assert_kill_switch(quotes_path, runs_root)
            _assert_no_lookahead()
    except SystemExit as exc:  # propagate process failures
        status = "FAIL"
        reason = f"exit_code={exc.code}"
    except Exception as exc:  # noqa: BLE001 - surface assertion details
        status = "FAIL"
        reason = str(exc)

    summary = f"SEMANTIC_LOOP_SUMMARY|status={status}|reason={reason}"
    print("===BEGIN===")
    print(summary)
    if status == "PASS":
        print("PASS: semantic training loop verified")
        print("===END===")
        print(summary)
        return

    print("===END===")
    print(summary)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
