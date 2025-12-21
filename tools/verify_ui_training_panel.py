from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stdio_utf8 import configure_stdio_utf8
from tools.ui_app import parse_training_markers, run_training_daemon


def _write_minimal_quotes(path: Path) -> None:
    rows = [
        {"ts_utc": "2024-01-01T00:00:00+00:00", "symbol": "MSFT", "price": "200", "source": "synthetic"},
        {"ts_utc": "2024-01-01T00:00:10+00:00", "symbol": "MSFT", "price": "202", "source": "synthetic"},
        {"ts_utc": "2024-01-01T00:00:20+00:00", "symbol": "MSFT", "price": "199", "source": "synthetic"},
    ]
    headers = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run() -> int:
    configure_stdio_utf8()
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        quotes_path = base / "quotes.csv"
        runs_root = base / "runs"
        _write_minimal_quotes(quotes_path)

        result, markers = run_training_daemon(3, input_path=quotes_path, runs_root=runs_root)
        output_blob = f"{result.stdout}\n{result.stderr}"
        errors: list[str] = []

        if result.returncode != 0:
            errors.append(f"train_daemon exit code {result.returncode}")

        combined_markers = markers or parse_training_markers(output_blob)
        marker_seen = "RUN_DIR=" in output_blob or "SUMMARY_PATH=" in output_blob
        marker_seen = marker_seen or "RUN_DIR" in combined_markers or "SUMMARY_PATH" in combined_markers

        if not marker_seen:
            errors.append("Expected RUN_DIR or SUMMARY_PATH markers in output")

        run_dir_text = combined_markers.get("RUN_DIR") if combined_markers else None
        summary_text = combined_markers.get("SUMMARY_PATH") if combined_markers else None
        if run_dir_text:
            print(f"RUN_DIR detected: {run_dir_text}")
            if not Path(run_dir_text).exists():
                errors.append("RUN_DIR path missing")
        if summary_text:
            print(f"SUMMARY_PATH detected: {summary_text}")
            if not Path(summary_text).exists():
                errors.append("SUMMARY_PATH file missing")

        if errors:
            for err in errors:
                print(f"FAIL: {err}")
            return 1

        print("PASS: training panel runner captured markers")
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
