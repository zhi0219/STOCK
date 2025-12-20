from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _write_events(logs_dir: Path) -> Path:
    now = datetime.now(timezone.utc)
    events_path = logs_dir / "events_verify_select.jsonl"
    events: List[dict] = [
        {
            "event_type": "MOVE",
            "symbol": "AAPL",
            "message": "AAPL spike after earnings beat",
            "ts_utc": _iso(now - timedelta(minutes=30)),
        },
        {
            "event_type": "DATA_STALE",
            "symbol": "MSFT",
            "message": "Latency increased for quote feed",
            "ts_utc": _iso(now - timedelta(minutes=20)),
        },
        {
            "event_type": "MOVE",
            "symbol": "MSFT",
            "message": "MSFT drift with low volume",
            "ts_utc": _iso(now - timedelta(minutes=10)),
        },
        {
            "event_type": "NEWS",
            "symbol": "AAPL",
            "message": "Apple guidance trimmed",
            "ts_utc": _iso(now - timedelta(minutes=15)),
        },
        {
            "event_type": "LATENCY",
            "symbol": "AAPL",
            "message": "Order book latency warning",
            "ts_utc": _iso(now - timedelta(minutes=5)),
        },
        {
            "event_type": "MOVE",
            "symbol": "TSLA",
            "message": "TSLA rebound",
            "ts_utc": _iso(now - timedelta(minutes=200)),
        },
    ]
    with events_path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return events_path


def _write_status(logs_dir: Path) -> Path:
    status_path = logs_dir / "status_verify.json"
    status_data = {
        "app": "verify_select_evidence",
        "ok": True,
        "note": "synthetic status",
    }
    status_path.write_text(json.dumps(status_data), encoding="utf-8")
    return status_path


def _parse_saved_path(stdout: str) -> Path:
    match = re.search(r"Saved to:\s*(.+)", stdout)
    if not match:
        return ROOT / "evidence_packs" / "missing_output.md"
    return Path(match.group(1).strip())


def run() -> int:
    logs_dir = ROOT / "Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    events_path = _write_events(logs_dir)
    status_path = _write_status(logs_dir)

    question = "哪些 AAPL 事件涉及 latency 或 earnings?"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "select_evidence.py"),
        "--question",
        question,
        "--since-minutes",
        "180",
        "--limit",
        "5",
        "--max-chars",
        "1200",
    ]

    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    errors: List[str] = []
    if result.returncode != 0:
        errors.append(f"select_evidence exited with {result.returncode}: {stderr}")

    required_sections = [
        "SYSTEM RULES",
        "STATUS SNAPSHOT",
        "SELECTED EVIDENCE",
        "QUESTION",
    ]
    for section in required_sections:
        if section not in stdout:
            errors.append(f"Missing section: {section}")

    evidence_tags = re.findall(r"\[evidence:[^\]]+\]", stdout)
    if len(evidence_tags) < 2:
        errors.append("Expected at least 2 evidence tags")

    if "latency" not in stdout.lower() or "aapl" not in stdout.lower():
        errors.append("Expected latency and AAPL evidence lines")

    # Ensure output respects the max-chars cap for the main packet (exclude trailing Saved to)
    packet_text = stdout.split("Saved to:")[0].strip()
    if len(packet_text) > 1200:
        errors.append("Output exceeded max-chars budget")

    saved_path = _parse_saved_path(stdout)

    for path in [events_path, status_path, saved_path]:
        try:
            if path.exists():
                if path.is_file():
                    path.unlink()
        except Exception:
            pass

    # Clean up parent dir if empty
    if saved_path.parent.exists() and not any(saved_path.parent.iterdir()):
        try:
            saved_path.parent.rmdir()
        except Exception:
            pass

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    print("PASS: verify_select_evidence completed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
