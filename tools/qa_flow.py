from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stdio_utf8 import configure_stdio_utf8


def _parse_saved_path(output: str) -> Optional[Path]:
    match = re.search(r"Saved to:\s*(.+)", output)
    if not match:
        return None
    return Path(match.group(1).strip())


def _run_select(question: str) -> Tuple[int, str, str, Optional[Path]]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "select_evidence.py"),
        "--question",
        question,
        "--since-minutes",
        "1440",
        "--limit",
        "30",
        "--max-chars",
        "12000",
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    evidence_path = _parse_saved_path(result.stdout)
    return result.returncode, result.stdout, result.stderr, evidence_path


def _run_make_packet(question: str, evidence_path: Optional[Path]) -> Tuple[int, str, str, Optional[Path]]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "make_ai_packet.py"),
        "--question",
        question,
    ]
    if evidence_path:
        cmd.extend(["--from-evidence-pack", str(evidence_path)])
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    packet_path = _parse_saved_path(result.stdout)
    return result.returncode, result.stdout, result.stderr, packet_path


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-shot QA workflow: evidence + AI packet")
    parser.add_argument("--question", required=True, help="The question to prepare for ChatGPT")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    configure_stdio_utf8()
    args = parse_args(argv)

    select_code, select_out, select_err, evidence_path = _run_select(args.question)
    if select_code != 0:
        print(f"[WARN] select_evidence exited with {select_code}")
        if select_err:
            print(select_err, file=sys.stderr)
    if evidence_path is None:
        print("[WARN] No evidence pack path detected (missing logs?)")
    else:
        print(f"Evidence pack: {evidence_path}")
        print(f"OUTPUT_EVIDENCE_PACK={evidence_path}")

    make_code, make_out, make_err, packet_path = _run_make_packet(args.question, evidence_path)
    if make_code != 0:
        print(f"[ERROR] make_ai_packet exited with {make_code}")
        if make_err:
            print(make_err, file=sys.stderr)
        return 1

    if packet_path:
        print(f"AI packet: {packet_path}")
        print(f"OUTPUT_PACKET={packet_path}")
    else:
        print("[WARN] AI packet path not detected")

    print("\n==== AI PACKET CONTENT (paste into ChatGPT) ====")
    print(make_out.strip())

    packet_for_command = packet_path if packet_path else Path("<packet.md>")
    capture_cmd = (
        ".\\.venv\\Scripts\\python.exe .\\tools\\capture_ai_answer.py "
        f"--packet \"{packet_for_command}\" --answer-file \"answer.md\""
    )
    print("\nNext step (capture AI answer):")
    print(capture_cmd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
