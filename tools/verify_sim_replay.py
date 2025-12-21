from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
SIM_REPLAY = ROOT / "tools" / "sim_replay.py"


def _write_quotes(path: Path) -> None:
    rows = [
        {"ts_utc": "2024-01-01T00:00:00+00:00", "symbol": "AAPL", "price": "100", "source": "synthetic"},
        {"ts_utc": "2024-01-01T00:00:01+00:00", "symbol": "AAPL", "price": "101", "source": "synthetic"},
        {
            "ts_utc": "2024-01-01T00:00:02+00:00",
            "symbol": "AAPL",
            "price": "102",
            "source": "synthetic",
            "data_status": "DATA_STALE",
        },
    ]
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_jsonl(path: Path) -> List[dict]:
    payload: List[dict] = []
    if not path.exists():
        return payload
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            payload.append(json.loads(line))
    return payload


def _require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}")
        sys.exit(1)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        quotes_path = base / "quotes.csv"
        logs_dir = base / "Logs"
        _write_quotes(quotes_path)

        cmd = [
            sys.executable,
            str(SIM_REPLAY),
            "--input",
            str(quotes_path),
            "--max-steps",
            "5",
            "--logs-dir",
            str(logs_dir),
        ]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            raise SystemExit(proc.returncode)

        equity_path = logs_dir / "equity_curve.jsonl"
        equity = _read_jsonl(equity_path)
        _require(len(equity) >= 3, "equity_curve.jsonl should have at least 3 rows")

        timestamps = [datetime.fromisoformat(row["ts_utc"]) for row in equity]
        _require(all(ts2 >= ts1 for ts1, ts2 in zip(timestamps, timestamps[1:])), "timestamps must be monotonic")

        sample = equity[-1]
        _require("mode" in sample and "drawdown_pct" in sample, "risk HUD fields missing in equity curve")

        orders_path = logs_dir / "orders_sim.jsonl"
        orders = _read_jsonl(orders_path)
        _require(len(orders) <= 1, "data_stale step should not create new orders")

        events = _read_jsonl(logs_dir / "events_sim.jsonl")
        stale_events = [ev for ev in events if ev.get("event_type") == "SIM_DECISION"]
        _require(stale_events, "risk rejection event should be recorded for stale data")

        print("PASS: sim replay verified")


if __name__ == "__main__":
    main()
