from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _write_synthetic_logs(logs_dir: Path) -> Tuple[Path, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    events_path = logs_dir / "events_verify_e2e.jsonl"
    events: List[dict] = [
        {
            "event_type": "MOVE",
            "symbol": "AAPL",
            "message": "AAPL volume spike with latency warning",
            "ts_utc": _iso(now - timedelta(minutes=5)),
        },
        {
            "event_type": "NEWS",
            "symbol": "MSFT",
            "message": "MSFT guidance updated",
            "ts_utc": _iso(now - timedelta(minutes=8)),
        },
    ]
    with events_path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    status_path = logs_dir / "status_verify_e2e.json"
    status_data = {"app": "verify_e2e", "ok": True, "note": "synthetic status"}
    status_path.write_text(json.dumps(status_data), encoding="utf-8")

    return events_path, status_path


def _parse_saved_path(stdout: str) -> Optional[Path]:
    match = re.search(r"Saved to:\s*(.+)", stdout)
    if not match:
        return None
    return Path(match.group(1).strip())


def _run_command(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def _cleanup(paths: List[Path]) -> None:
    dirs_to_check: List[Path] = []
    for path in paths:
        try:
            if path.is_file():
                parent = path.parent
                path.unlink()
                dirs_to_check.append(parent)
            elif path.is_dir():
                dirs_to_check.append(path)
        except Exception:
            pass

    for d in dirs_to_check:
        try:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        except Exception:
            pass


def run() -> int:
    logs_dir = ROOT / "Logs"
    events_path, status_path = _write_synthetic_logs(logs_dir)

    temp_artifacts: List[Path] = [events_path, status_path]
    errors: List[str] = []

    question = "合成验收：最近有什么关键事件？"

    select_cmd = [
        sys.executable,
        str(ROOT / "tools" / "select_evidence.py"),
        "--question",
        question,
        "--limit",
        "10",
        "--since-minutes",
        "60",
    ]
    select_result = _run_command(select_cmd)
    select_stdout = select_result.stdout
    evidence_path = _parse_saved_path(select_stdout)
    if select_result.returncode != 0:
        errors.append(f"select_evidence exit {select_result.returncode}: {select_result.stderr}")
    if not evidence_path or not evidence_path.exists():
        errors.append("evidence pack not generated")
    else:
        temp_artifacts.append(evidence_path)
    if "[evidence:" not in select_stdout:
        errors.append("evidence markers missing from select output")

    make_cmd = [
        sys.executable,
        str(ROOT / "tools" / "make_ai_packet.py"),
        "--question",
        question,
    ]
    if evidence_path:
        make_cmd.extend(["--from-evidence-pack", str(evidence_path)])
    make_result = _run_command(make_cmd)
    packet_path = _parse_saved_path(make_result.stdout)
    if make_result.returncode != 0:
        errors.append(f"make_ai_packet exit {make_result.returncode}: {make_result.stderr}")
    if not packet_path or not packet_path.exists():
        errors.append("ai packet not generated")
    else:
        temp_artifacts.append(packet_path)
    if "SYSTEM RULES" not in make_result.stdout:
        errors.append("SYSTEM RULES missing in packet output")
    if "REQUIRED OUTPUT FORMAT" not in make_result.stdout:
        errors.append("required output section missing")

    if not packet_path or not packet_path.exists():
        _cleanup(temp_artifacts)
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    evidence_tags = re.findall(r"\[evidence:[^\]]+\]", select_stdout)
    while len(evidence_tags) < 2:
        evidence_tags.append("[evidence: synthetic#L1]")
    answer_text = (
        "结论要点：合成事件总结。 "
        f"{evidence_tags[0]}\n"
        "硬事实：保持只读和风险提示。 "
        f"{evidence_tags[1]}\n"
        "主流一句：近期事件涉及延迟。\n"
        "反方一句：暂无相反证据。\n"
        "风险提醒：仅供观察，不含交易建议。"
    )

    capture_cmd = [
        sys.executable,
        str(ROOT / "tools" / "capture_ai_answer.py"),
        "--packet",
        str(packet_path),
        "--answer-text",
        answer_text,
    ]
    capture_result = _run_command(capture_cmd)
    if capture_result.returncode != 0:
        errors.append(f"capture_ai_answer (non-strict) exit {capture_result.returncode}: {capture_result.stderr}")
    saved_answer_match = re.search(r"Saved answer to:\s*(.+)", capture_result.stdout)
    answer_path = Path(saved_answer_match.group(1).strip()) if saved_answer_match else None
    if answer_path and answer_path.exists():
        temp_artifacts.append(answer_path)
    else:
        errors.append("answer file not created")

    replay_cmd = [
        sys.executable,
        str(ROOT / "tools" / "replay_events.py"),
        "--type",
        "AI_ANSWER",
        "--limit",
        "5",
    ]
    replay_result = _run_command(replay_cmd)
    if replay_result.returncode != 0:
        errors.append(f"replay_events exit {replay_result.returncode}: {replay_result.stderr}")
    if "AI_ANSWER" not in replay_result.stdout:
        errors.append("AI_ANSWER not found in replay output")

    bad_answer_text = "买入并设定目标价，仓位 50% [evidence: synthetic#L2]"
    bad_capture_cmd = [
        sys.executable,
        str(ROOT / "tools" / "capture_ai_answer.py"),
        "--packet",
        str(packet_path),
        "--answer-text",
        bad_answer_text,
        "--strict",
    ]
    bad_result = _run_command(bad_capture_cmd)
    if bad_result.returncode != 2:
        errors.append(f"strict capture should exit 2, got {bad_result.returncode}")
    bad_answer_match = re.search(r"Saved answer to:\s*(.+)", bad_result.stdout)
    if bad_answer_match:
        temp_artifacts.append(Path(bad_answer_match.group(1)))

    for path in list(temp_artifacts):
        if path.parent.name == "qa_packets" and path.parent.parent == ROOT:
            temp_artifacts.append(path.parent)
        if path.parent.name == "qa_answers" and path.parent.parent == ROOT:
            temp_artifacts.append(path.parent)

    _cleanup(list(dict.fromkeys(temp_artifacts)))

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    print("PASS: verify_e2e_qa_loop completed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
