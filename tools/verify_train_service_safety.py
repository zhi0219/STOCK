import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TRAIN_SERVICE = ROOT / "tools" / "train_service.py"
RUNS_ROOT = ROOT / "Logs" / "train_runs" / "_service_verify"
SERVICE_STATE = ROOT / "Logs" / "train_service" / "state.json"
SERVICE_KILL_SWITCH = ROOT / "Logs" / "train_service" / "KILL_SWITCH"
SERVICE_ROLLING_SUMMARY = ROOT / "Logs" / "train_service" / "rolling_summary.md"


def _write_quotes(path: Path) -> None:
    rows = [
        {"ts_utc": "2024-01-01T00:00:00+00:00", "symbol": "TEST", "price": "100", "source": "synthetic"},
        {"ts_utc": "2024-01-01T00:00:05+00:00", "symbol": "TEST", "price": "101", "source": "synthetic"},
        {"ts_utc": "2024-01-01T00:00:10+00:00", "symbol": "TEST", "price": "99", "source": "synthetic"},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _cleanup() -> None:
    shutil.rmtree(RUNS_ROOT, ignore_errors=True)
    if SERVICE_KILL_SWITCH.exists():
        SERVICE_KILL_SWITCH.unlink()
    if SERVICE_ROLLING_SUMMARY.exists():
        SERVICE_ROLLING_SUMMARY.unlink()


def _run_service(quotes_path: Path) -> str:
    cmd = [
        sys.executable,
        str(TRAIN_SERVICE),
        "--episode-seconds",
        "2",
        "--max-episodes-per-hour",
        "2",
        "--max-episodes-per-day",
        "2",
        "--cooldown-seconds-between-episodes",
        "1",
        "--input",
        str(quotes_path),
        "--runs-root",
        str(RUNS_ROOT),
    ]
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    stdout = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        print(stdout)
        raise SystemExit(proc.returncode)
    return stdout


def _assert_outputs() -> None:
    if not SERVICE_STATE.exists():
        raise AssertionError("state.json not written")
    state = json.loads(SERVICE_STATE.read_text(encoding="utf-8"))
    run_dir = Path(str(state.get("last_run_dir"))) if state.get("last_run_dir") else None
    summary_path = Path(str(state.get("last_summary_path"))) if state.get("last_summary_path") else None
    if not run_dir or not run_dir.exists():
        raise AssertionError("run_dir missing from state")
    if not summary_path or not summary_path.exists():
        raise AssertionError("summary missing from state")

    run_files = list(run_dir.glob("*.md"))
    if not run_files:
        raise AssertionError("expected run outputs in run_dir")


def _assert_markers(stdout: str) -> None:
    for marker in ["SERVICE_START", "EPISODE_START", "EPISODE_END", "SERVICE_STOP"]:
        if marker not in stdout:
            raise AssertionError(f"missing marker: {marker}")


def _assert_kill_switch(quotes_path: Path) -> None:
    cmd = [
        sys.executable,
        str(TRAIN_SERVICE),
        "--episode-seconds",
        "5",
        "--max-episodes-per-hour",
        "3",
        "--max-episodes-per-day",
        "5",
        "--cooldown-seconds-between-episodes",
        "1",
        "--input",
        str(quotes_path),
        "--runs-root",
        str(RUNS_ROOT),
    ]
    proc = subprocess.Popen(
        cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
    )
    time.sleep(1)
    SERVICE_KILL_SWITCH.parent.mkdir(parents=True, exist_ok=True)
    SERVICE_KILL_SWITCH.write_text("STOP", encoding="utf-8")
    out, err = proc.communicate(timeout=30)
    output_blob = (out or "") + (err or "")
    if "SERVICE_STOP" not in output_blob:
        raise AssertionError("SERVICE_STOP missing after kill switch")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        quotes_path = Path(tmpdir) / "quotes.csv"
        _write_quotes(quotes_path)

        _cleanup()
        stdout = _run_service(quotes_path)
        _assert_markers(stdout)
        _assert_outputs()

        _assert_kill_switch(quotes_path)

    _cleanup()
    print("PASS: train service safety verified")


if __name__ == "__main__":
    main()
