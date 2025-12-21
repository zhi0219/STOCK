"""Verify QA packet path parsing and UTF-8 safe subprocess capture.

This script is stdlib-only and synthesizes minimal logs to exercise qa_flow.py
and the UI's subprocess capture behavior.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parent.parent


def _utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _write_synthetic_logs(logs_dir: Path) -> Tuple[Path, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    status_path = logs_dir / "status_verify_ui_qapacket.json"
    events_path = logs_dir / f"events_{now:%Y-%m-%d}_verify_ui_qapacket.jsonl"

    status_payload = {"system": "test", "ts": _iso(now)}
    status_path.write_text(json.dumps(status_payload), encoding="utf-8")

    events = [
        {"event_type": "NEWS", "symbol": "TST", "message": "Synthetic 😀 event", "ts_utc": _iso(now)},
        {
            "event_type": "MOVE",
            "symbol": "TST",
            "message": "Emoji ensures utf8 handling 🛰️",
            "ts_utc": _iso(now),
        },
    ]
    with events_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return status_path, events_path


def _run_utf8(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
    )


def _parse_packet_paths(stdout: str) -> Tuple[Path | None, Path | None]:
    packet_path = None
    evidence_path = None
    for line in stdout.splitlines():
        if line.startswith("PACKET_PATH="):
            packet_path = Path(line.split("PACKET_PATH=", 1)[1].strip())
        elif line.startswith("EVIDENCE_PACK_PATH="):
            evidence_path = Path(line.split("EVIDENCE_PACK_PATH=", 1)[1].strip())
        elif line.startswith("OUTPUT_PACKET="):
            packet_path = Path(line.split("OUTPUT_PACKET=", 1)[1].strip())
        elif line.startswith("OUTPUT_EVIDENCE_PACK="):
            evidence_path = Path(line.split("OUTPUT_EVIDENCE_PACK=", 1)[1].strip())
    return packet_path, evidence_path


def _assert_no_unicode_decode_error(proc: subprocess.CompletedProcess[str], label: str) -> list[str]:
    errors: list[str] = []
    marker = "UnicodeDecodeError"
    for stream_name, payload in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        if payload and marker in payload:
            errors.append(f"{label} {stream_name} contained decode error text")
    return errors


def run() -> int:
    logs_dir = ROOT / "Logs"
    status_path, events_path = _write_synthetic_logs(logs_dir)
    question = "Synthetic question with emoji 🚀"
    qa_cmd = [sys.executable, str(ROOT / "tools" / "qa_flow.py"), "--question", question]

    qa_proc = _run_utf8(qa_cmd)
    errors: list[str] = []

    packet_path, evidence_path = _parse_packet_paths(qa_proc.stdout or "")
    if qa_proc.returncode != 0:
        errors.append(f"qa_flow exited {qa_proc.returncode}: {qa_proc.stderr}")
    if packet_path is None:
        errors.append("PACKET_PATH marker missing from qa_flow stdout")
    errors.extend(_assert_no_unicode_decode_error(qa_proc, "qa_flow"))

    # Simulate the UI's subprocess capture behaviour.
    ui_proc = _run_utf8(qa_cmd)
    ui_packet_path, ui_evidence_path = _parse_packet_paths(ui_proc.stdout or "")
    if ui_proc.returncode != 0:
        errors.append(f"UI capture exited {ui_proc.returncode}: {ui_proc.stderr}")
    if ui_packet_path is None:
        errors.append("UI capture did not parse PACKET_PATH")
    errors.extend(_assert_no_unicode_decode_error(ui_proc, "ui_capture"))

    if evidence_path and not evidence_path.exists():
        errors.append("EVIDENCE_PACK_PATH missing on disk")
    if packet_path and not packet_path.exists():
        errors.append("PACKET_PATH missing on disk")

    # Cleanup artifacts
    for path in (status_path, events_path, packet_path, evidence_path):
        if path and path.exists():
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    for child in path.iterdir():
                        try:
                            child.unlink()
                        except Exception:
                            pass
                    path.rmdir()
            except Exception:
                pass

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    print("PASS: verify_ui_qapacket_path")
    return 0


if __name__ == "__main__":
    sys.exit(run())
