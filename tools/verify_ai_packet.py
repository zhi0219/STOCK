from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"


def _build_event(event_id: str, minutes_offset: int, message: str) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_offset)
    return {
        "event_id": event_id,
        "ts_utc": ts.isoformat(),
        "event_type": "TEST",
        "symbol": "TST",
        "severity": "info",
        "message": message,
    }


def _write_synthetic_events(path: Path) -> None:
    events = [
        _build_event("evt-1", 1, "first synthetic event"),
        _build_event("evt-2", 0, "second synthetic event"),
    ]
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _write_synthetic_status(path: Path) -> None:
    status = {
        "state": "ok",
        "last_event_ts": datetime.now(timezone.utc).isoformat(),
        "note": "synthetic status for verify_ai_packet",
    }
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_make_ai_packet(question: str) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "make_ai_packet.py"),
        "--question",
        question,
        "--limit",
        "10",
        "--since-minutes",
        "60",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)


def _find_new_packet(before: List[Path]) -> Path:
    packets_dir = ROOT / "qa_packets"
    if not packets_dir.exists():
        raise FileNotFoundError("qa_packets directory missing after generation")

    after = set(packets_dir.rglob("*.md"))
    before_set = set(before)
    new_files = sorted(after - before_set, key=lambda p: p.stat().st_mtime)
    if not new_files:
        raise FileNotFoundError("No new AI packet generated")
    return new_files[-1]


def _validate_packet(path: Path, question: str) -> None:
    text = path.read_text(encoding="utf-8")
    if "SYSTEM RULES" not in text:
        raise AssertionError("SYSTEM RULES section missing")
    if text.count("[evidence:") < 2:
        raise AssertionError("Expected at least two evidence references")
    if question not in text:
        raise AssertionError("Question text missing in packet")


def _cleanup(paths: List[Path]) -> None:
    for path in paths:
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            for child in list(path.iterdir()):
                _cleanup([child])
            try:
                path.rmdir()
            except OSError:
                pass


def main() -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    synthetic_events = LOGS_DIR / "events_2099-12-31_verify_ai_packet.jsonl"
    synthetic_status = LOGS_DIR / "_tmp_status_test.json"

    existing_packets = list((ROOT / "qa_packets").rglob("*.md")) if (ROOT / "qa_packets").exists() else []
    question = "验证 AI 证据包流水线是否正常？"

    new_packet: Optional[Path] = None

    try:
        _write_synthetic_events(synthetic_events)
        _write_synthetic_status(synthetic_status)

        result = _run_make_ai_packet(question)
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            print("FAIL: make_ai_packet returned non-zero exit code")
            print(output.rstrip())
            return 1

        try:
            new_packet = _find_new_packet(existing_packets)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"FAIL: {exc}")
            print(output.rstrip())
            return 1

        try:
            _validate_packet(new_packet, question)
        except AssertionError as exc:
            print(f"FAIL: {exc}")
            print(output.rstrip())
            return 1

        print("PASS: AI packet generation verified")
        return 0
    finally:
        _cleanup([synthetic_events, synthetic_status])
        if new_packet and new_packet.exists():
            parent = new_packet.parent
            _cleanup([new_packet])
            if parent.exists() and not any(parent.iterdir()):
                _cleanup([parent])


if __name__ == "__main__":
    sys.exit(main())
