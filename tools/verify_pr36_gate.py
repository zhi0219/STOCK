from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARTIFACTS_DIR = Path("artifacts")
LOG_PATH = ARTIFACTS_DIR / "compile_check.log"
RESULT_PATH = ARTIFACTS_DIR / "compile_check_result.json"


def _safe_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _extract_exception(output: str) -> str | None:
    if not output:
        return None
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1].strip()


def _extract_syntax_details(output: str) -> dict[str, object] | None:
    if not output:
        return None
    lines = output.splitlines()
    for idx, line in enumerate(lines):
        if "File \"" in line and "line" in line:
            parts = line.strip().split(",")
            file_path = None
            line_no = None
            if parts:
                file_part = parts[0]
                if "File" in file_part:
                    file_path = file_part.split("File", 1)[-1].strip().strip("\"")
            if len(parts) > 1:
                line_part = parts[1].strip()
                if line_part.startswith("line"):
                    try:
                        line_no = int(line_part.split()[1])
                    except (IndexError, ValueError):
                        line_no = None
            code_line = None
            if idx + 1 < len(lines):
                code_line = lines[idx + 1].strip()
            return {
                "file": file_path,
                "line": line_no,
                "code": code_line,
            }
    return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "compileall", "-q", "tools"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = "\n".join(block for block in [result.stdout, result.stderr] if block)
    _safe_write_text(LOG_PATH, combined)

    exception_summary = _extract_exception(combined) if result.returncode != 0 else None
    syntax_details = _extract_syntax_details(combined) if result.returncode != 0 else None
    payload: dict[str, Any] = {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "returncode": result.returncode,
        "command": cmd,
        "exception_summary": exception_summary,
        "error_location": syntax_details,
        "python_version": platform.python_version(),
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write_json(RESULT_PATH, payload)

    if result.returncode != 0:
        print("verify_pr36_gate FAIL")
        if exception_summary:
            print(f" - {exception_summary}")
        return 1

    print("verify_pr36_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
