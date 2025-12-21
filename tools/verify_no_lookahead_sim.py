from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM_REPLAY = ROOT / "tools" / "sim_replay.py"


def _write_monotonic_quotes(path: Path) -> None:
    rows = [
        {"ts_utc": "2024-02-01T00:00:00+00:00", "symbol": "MSFT", "price": "200", "source": "synthetic"},
        {"ts_utc": "2024-02-01T00:00:01+00:00", "symbol": "MSFT", "price": "201", "source": "synthetic"},
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        quotes_path = base / "quotes.csv"
        logs_dir = base / "Logs"
        _write_monotonic_quotes(quotes_path)

        cmd = [
            sys.executable,
            str(SIM_REPLAY),
            "--input",
            str(quotes_path),
            "--max-steps",
            "2",
            "--logs-dir",
            str(logs_dir),
            "--verify-no-lookahead",
        ]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            sys.exit(proc.returncode)
        print("PASS: no lookahead verified")


if __name__ == "__main__":
    main()
