from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TRAIN_DAEMON = ROOT / "tools" / "train_daemon.py"
NO_LOOKAHEAD = ROOT / "tools" / "verify_no_lookahead_sim.py"
DATA_DIR = ROOT / "Data"
KILL_SWITCH = DATA_DIR / "KILL_SWITCH"


def _write_minimal_quotes(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts_utc": "2024-01-01T00:00:00+00:00", "symbol": "MSFT", "price": "200", "source": "synthetic"},
        {"ts_utc": "2024-01-01T00:00:10+00:00", "symbol": "MSFT", "price": "202", "source": "synthetic"},
        {"ts_utc": "2024-01-01T00:00:20+00:00", "symbol": "MSFT", "price": "199", "source": "synthetic"},
    ]
    headers = list(rows[0].keys())
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _run_train(max_runtime: int, max_steps: int, max_trades: int, runs_root: Path, quotes_path: Path) -> Path:
    cmd = [
        sys.executable,
        str(TRAIN_DAEMON),
        "--input",
        str(quotes_path),
        "--max-runtime-seconds",
        str(max_runtime),
        "--max-steps",
        str(max_steps),
        "--max-trades",
        str(max_trades),
        "--max-log-mb",
        "5",
        "--runs-root",
        str(runs_root),
    ]
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise SystemExit(proc.returncode)
    markers = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            markers[key.strip()] = value.strip()
    if "RUN_DIR" not in markers:
        raise AssertionError("RUN_DIR marker missing")
    return Path(markers["RUN_DIR"])


def _assert_outputs(run_dir: Path) -> None:
    if not run_dir.exists():
        raise AssertionError("run_dir missing")
    meta_path = run_dir / "run_meta.json"
    summary_path = run_dir / "summary.md"
    if not meta_path.exists() or not summary_path.exists():
        raise AssertionError("run_meta or summary missing")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    outputs = [run_dir / "equity_curve.csv", run_dir / "orders_sim.jsonl"]
    if not any(path.exists() for path in outputs):
        raise AssertionError("expected equity curve or orders output")
    if not meta.get("stop_reason"):
        raise AssertionError("stop_reason not recorded")


def _assert_stop_reason(run_dir: Path, expected: str) -> None:
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    if meta.get("stop_reason") != expected:
        raise AssertionError(f"stop_reason mismatch: {meta.get('stop_reason')} != {expected}")


def _assert_kill_switch_trip(runs_root: Path, quotes_path: Path) -> None:
    try:
        KILL_SWITCH.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH.write_text("TEST", encoding="utf-8")
        run_dir = _run_train(
            max_runtime=10, max_steps=10, max_trades=2, runs_root=runs_root, quotes_path=quotes_path
        )
        _assert_stop_reason(run_dir, "kill_switch")
    finally:
        if KILL_SWITCH.exists():
            KILL_SWITCH.unlink()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        runs_root = base / "runs"
        quotes_path = base / "quotes.csv"
        _write_minimal_quotes(quotes_path)

        run_dir = _run_train(
            max_runtime=10, max_steps=500, max_trades=5, runs_root=runs_root, quotes_path=quotes_path
        )
        _assert_outputs(run_dir)

        tiny_run = _run_train(max_runtime=5, max_steps=1, max_trades=10, runs_root=runs_root, quotes_path=quotes_path)
        _assert_stop_reason(tiny_run, "max_steps")

        _assert_kill_switch_trip(runs_root, quotes_path)

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

    print("PASS: train daemon safety verified")


if __name__ == "__main__":
    main()
