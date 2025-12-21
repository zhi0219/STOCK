from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _write_synthetic_logs(logs_dir: Path) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    events_path = logs_dir / f"events_{now:%Y-%m-%d}_ui_actions.jsonl"
    events = [
        {"event_type": "NEWS", "symbol": "SYNTH", "message": "Synthetic log for UI", "ts_utc": _iso(now)},
        {
            "event_type": "MOVE",
            "symbol": "SYNTH",
            "message": "Synthetic move event",
            "ts_utc": _iso(now - timedelta(minutes=2)),
        },
    ]
    with events_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    return events_path


def _run(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def _parse_output_paths(stdout: str) -> Tuple[Path | None, Path | None]:
    packet_path = None
    evidence_path = None
    for line in stdout.splitlines():
        if line.startswith("OUTPUT_PACKET="):
            packet_path = Path(line.split("=", 1)[1].strip())
        elif line.startswith("OUTPUT_EVIDENCE_PACK="):
            evidence_path = Path(line.split("=", 1)[1].strip())
        elif line.startswith("AI packet:"):
            packet_path = Path(line.split(":", 1)[1].strip())
        elif line.startswith("Evidence pack:"):
            evidence_path = Path(line.split(":", 1)[1].strip())
    return packet_path, evidence_path


def _cleanup(paths: List[Path]) -> None:
    for path in paths:
        try:
            if path.is_file():
                path.unlink()
        except Exception:
            pass
    for path in paths:
        try:
            if path.is_dir() and path.exists() and not any(path.iterdir()):
                path.rmdir()
        except Exception:
            pass


def run() -> int:
    logs_dir = ROOT / "Logs"
    events_path = _write_synthetic_logs(logs_dir)
    artifacts: List[Path] = [events_path]
    errors: List[str] = []

    question = "UI actions synthetic question"
    qa_cmd = [sys.executable, str(ROOT / "tools" / "qa_flow.py"), "--question", question]
    qa_result = _run(qa_cmd)
    packet_path, evidence_path = _parse_output_paths(qa_result.stdout)
    if qa_result.returncode != 0:
        errors.append(f"qa_flow exit {qa_result.returncode}: {qa_result.stderr}")
    if packet_path and packet_path.exists():
        artifacts.append(packet_path)
    else:
        errors.append("packet not generated")
    if evidence_path and evidence_path.exists():
        artifacts.append(evidence_path)

    if not packet_path:
        _cleanup(artifacts)
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    answer_text = (
        "结论要点：synthetic answer."\
        " 确保包含引用 [evidence: synthetic#1] [evidence: synthetic#2]\n"
        "硬事实：保持只读，避免交易建议。"
    )
    capture_cmd = [
        sys.executable,
        str(ROOT / "tools" / "capture_ai_answer.py"),
        "--packet",
        str(packet_path),
        "--answer-text",
        answer_text,
    ]
    capture_result = _run(capture_cmd)
    if capture_result.returncode != 0:
        errors.append(f"capture_ai_answer exit {capture_result.returncode}: {capture_result.stderr}")
    answer_match = re.search(r"Saved answer to:\s*(.+)", capture_result.stdout)
    answer_path = Path(answer_match.group(1)) if answer_match else None
    if answer_path and answer_path.exists():
        artifacts.append(answer_path)
    else:
        errors.append("answer not saved")

    strict_cmd = capture_cmd.copy()
    strict_cmd[-1] = answer_text + " 买入信号"  # append trade hint
    strict_cmd.append("--strict")
    strict_result = _run(strict_cmd)
    if strict_result.returncode != 2:
        errors.append(f"strict capture should exit 2, got {strict_result.returncode}")

    appended = "AI_ANSWER" in capture_result.stdout or "AI_ANSWER" in strict_result.stdout
    if not appended:
        events_file = capture_result.stdout.split("Appended event to:")
        if len(events_file) > 1:
            candidate = events_file[1].strip().splitlines()[0].strip()
            events_path = Path(candidate)
            if events_path.exists():
                content = events_path.read_text(encoding="utf-8")
                if "AI_ANSWER" in content:
                    appended = True
    if not appended:
        errors.append("AI_ANSWER event not observed")

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        _cleanup(artifacts)
        return 1

    print("PASS: verify_ui_actions completed")
    _cleanup(artifacts)
    return 0


if __name__ == "__main__":
    sys.exit(run())
