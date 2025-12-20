from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"


def _build_event(event_id: str, ts_offset_minutes: int, *, symbol: str, event_type: str, severity: str, message: str) -> dict:
    ts = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=ts_offset_minutes)
    return {
        "event_id": event_id,
        "ts_utc": ts.isoformat(),
        "symbol": symbol,
        "event_type": event_type,
        "severity": severity,
        "message": message,
    }


def _write_synthetic_events(logs_dir: Path) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    events_path = logs_dir / "events_2099-01-01.jsonl"
    events: List[dict] = [
        _build_event("evt-1", 5, symbol="AAPL", event_type="NEWS", severity="info", message="headline moved quickly"),
        _build_event("evt-2", 4, symbol="MSFT", event_type="ALERT", severity="warn", message="cooldown triggered"),
        _build_event("evt-3", 3, symbol="AAPL", event_type="ALERT", severity="error", message="repeat attempts detected"),
    ]
    with events_path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return events_path


def _run_brief(logs_dir: Path, output_dir: Path, *, date_override: str) -> Tuple[int, Path, str, str]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "brief_report.py"),
        "--logs-dir",
        str(logs_dir),
        "--output-dir",
        str(output_dir),
        "--limit",
        "10",
        "--date",
        date_override,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    report_path = output_dir / f"{date_override}.md"
    return result.returncode, report_path, result.stdout, result.stderr


def _validate_report(report_path: Path) -> Tuple[bool, str]:
    if not report_path.exists():
        return False, f"report file missing: {report_path}"

    text = report_path.read_text(encoding="utf-8")
    required_sections = ["## Facts", "## Analysis", "## Hypotheses", "## Next tests"]
    for section in required_sections:
        if section not in text:
            return False, f"missing section: {section}"

    evidence_count = text.count("[evidence:")
    if evidence_count < 2:
        return False, "report missing sufficient evidence references"

    return True, "report structure and evidence verified"


def cleanup(paths: List[Path]) -> None:
    for path in paths:
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            for child in path.iterdir():
                if child.is_file():
                    child.unlink()
                else:
                    cleanup([child])
            path.rmdir()


def main() -> int:
    temp_output = LOGS_DIR / "_tmp_reports"
    date_override = "2099-01-01"

    synthetic_path = None
    try:
        synthetic_path = _write_synthetic_events(LOGS_DIR)
        code, report_path, stdout, stderr = _run_brief(LOGS_DIR, temp_output, date_override=date_override)
        if code != 0:
            print("FAIL: brief_report.py returned non-zero", file=sys.stderr)
            print(stdout)
            print(stderr, file=sys.stderr)
            return 1

        ok, message = _validate_report(report_path)
        if not ok:
            print(f"FAIL: {message}", file=sys.stderr)
            return 1

        print("PASS: brief report generation verified")
        return 0
    finally:
        cleanup([p for p in [synthetic_path, temp_output] if p is not None])


if __name__ == "__main__":
    sys.exit(main())
