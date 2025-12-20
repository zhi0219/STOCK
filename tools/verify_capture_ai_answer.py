from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"


def _write_packet(path: Path) -> None:
    content = """# AI 问答证据包

## A) SYSTEM RULES
- 示例规则

## B) EVIDENCE
- [evidence: events_20240101.jsonl#L1]
- [evidence: events_20240101.jsonl#L2]

## C) QUESTION
- 测试问题？

## D) REQUIRED OUTPUT FORMAT
- 结论要点
- 硬事实
- 主流一句
- 反方一句
- 风险提醒
"""
    path.write_text(content, encoding="utf-8")


def _build_answer_text(good: bool) -> str:
    if good:
        return """结论要点：以证据支撑的总结。[evidence: events_20240101.jsonl#L1]
硬事实：事实列点。[evidence: events_20240101.jsonl#L2]
主流一句：主流观点覆盖。
反方一句：提供反方角度。
风险提醒：关注数据滞后风险。
"""
    return """结论要点：考虑买入机会。[evidence: events_20240101.jsonl#L1]
硬事实：价格突破，目标价待定。[evidence: events_20240101.jsonl#L2]
主流一句：市场情绪乐观。
反方一句：估值高。
风险提醒：仓位控制。
"""


def _run_capture(packet: Path, answer_text: str, out_dir: Path, strict: bool = False) -> subprocess.CompletedProcess:
    cmd: List[str] = [
        sys.executable,
        str(ROOT / "tools" / "capture_ai_answer.py"),
        "--packet",
        str(packet),
        "--answer-text",
        answer_text,
        "--out-dir",
        str(out_dir),
    ]
    if strict:
        cmd.append("--strict")
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)


def _find_latest_answer(out_dir: Path) -> Path:
    candidates = sorted(out_dir.glob("*_answer.md"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError("No answer file generated")
    return candidates[-1]


def _read_last_event(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        lines = [line for line in f.read().splitlines() if line.strip()]
    if not lines:
        raise AssertionError("No events recorded")
    return json.loads(lines[-1])


def main() -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="capture_test_", dir=str(ROOT)))
    packet_path = tmp_dir / "packet.md"
    out_dir = tmp_dir / "answers"
    events_path = LOGS_DIR / "events_2999-12-31_capture_test.jsonl"

    try:
        if events_path.exists():
            events_path.unlink()
        _write_packet(packet_path)
        events_path.write_text("", encoding="utf-8")

        good_answer = _build_answer_text(good=True)
        result = _run_capture(packet_path, good_answer, out_dir)
        if result.returncode != 0:
            print("FAIL: capture_ai_answer returned non-zero for good answer")
            print((result.stdout or "") + (result.stderr or ""))
            return 1

        answer_file = _find_latest_answer(out_dir)
        if not answer_file.exists():
            print("FAIL: answer file not created")
            return 1

        if not events_path.exists():
            print("FAIL: events file not created")
            return 1

        last_event = _read_last_event(events_path)
        metrics = last_event.get("metrics", {})
        if not metrics.get("has_citations"):
            print("FAIL: has_citations should be True for good answer")
            return 1
        if metrics.get("has_trade_advice"):
            print("FAIL: has_trade_advice should be False for good answer")
            return 1
        if last_event.get("event_type") != "AI_ANSWER":
            print("FAIL: event_type mismatch")
            return 1

        bad_answer = _build_answer_text(good=False)
        strict_result = _run_capture(packet_path, bad_answer, out_dir, strict=True)
        if strict_result.returncode != 2:
            print("FAIL: strict mode should exit with code 2 when trade advice detected")
            print((strict_result.stdout or "") + (strict_result.stderr or ""))
            return 1

        print("PASS: capture_ai_answer workflow verified")
        return 0
    finally:
        for path in sorted(out_dir.glob("*_answer.md")):
            path.unlink(missing_ok=True)
        if out_dir.exists():
            out_dir.rmdir()
        if packet_path.exists():
            packet_path.unlink()
        if tmp_dir.exists():
            try:
                tmp_dir.rmdir()
            except OSError:
                pass
        events_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
