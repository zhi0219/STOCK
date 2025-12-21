from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stdio_utf8 import configure_stdio_utf8


def _write_synthetic_logs() -> Tuple[Path, Path]:
    logs_dir = ROOT / "Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)

    events_path = logs_dir / "events_utf8_stdio.jsonl"
    events = [
        {
            "event_type": "NEWS",
            "symbol": "UTF",
            "message": "UTF-8 ✅ 验证：多语言输出",
            "ts_utc": now.isoformat(),
        },
        {
            "event_type": "HEALTH",
            "symbol": "UTF",
            "message": "回归警戒 🚨 测试，保持只读",
            "ts_utc": now.isoformat(),
        },
    ]
    with events_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    status_path = logs_dir / "status_utf8_stdio.json"
    status_payload = {
        "source": "verify_utf8_stdio",
        "note": "UTF-8 stdio regression fixture",
        "ts_utc": now.isoformat(),
    }
    status_path.write_text(json.dumps(status_payload, ensure_ascii=False), encoding="utf-8")

    return events_path, status_path


def _run_cp1252(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    env["PYTHONUTF8"] = "0"
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)


def _assert_output(name: str, result: subprocess.CompletedProcess[str], markers: List[str]) -> List[str]:
    failures: List[str] = []
    if result.returncode != 0:
        failures.append(f"{name} exited {result.returncode}")
    for marker in markers:
        if marker not in (result.stdout or ""):
            failures.append(f"{name} output missing marker: {marker}")
    if "✅" not in (result.stdout or "") and "🚨" not in (result.stdout or ""):
        failures.append(f"{name} output missing expected emoji")
    return failures


def main(argv: List[str] | None = None) -> int:  # pragma: no cover - invoked by verify suite
    configure_stdio_utf8()
    _ = argv  # unused but kept for symmetry

    events_path, status_path = _write_synthetic_logs()
    _ = status_path  # silence linters

    select_cmd = [
        sys.executable,
        str(ROOT / "tools" / "select_evidence.py"),
        "--question",
        "UTF-8 ✅ 验证提问",
        "--since-minutes",
        "120",
        "--limit",
        "5",
    ]
    select_result = _run_cp1252(select_cmd)
    select_failures = _assert_output("select_evidence", select_result, ["SYSTEM RULES", "EVIDENCE"])

    packet_cmd = [
        sys.executable,
        str(ROOT / "tools" / "make_ai_packet.py"),
        "--question",
        "UTF-8 ✅ 验证提问",
        "--since-minutes",
        "120",
        "--limit",
        "5",
    ]
    packet_result = _run_cp1252(packet_cmd)
    packet_failures = _assert_output(
        "make_ai_packet", packet_result, ["SYSTEM RULES", "REQUIRED OUTPUT FORMAT"]
    )

    failures = select_failures + packet_failures

    if failures:
        for fail in failures:
            print(f"FAIL: {fail}")
        return 1

    print("PASS: UTF-8 stdio verified under cp1252 pipe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
