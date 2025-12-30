from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_TARGETS = ["tools"]
LOG_FILENAME = "compile_check.log"
RESULT_FILENAME = "compile_check_result.json"

ERROR_COMPILE_RE = re.compile(r"Error compiling '([^']+)'", re.IGNORECASE)
FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)')
SYNTAX_RE = re.compile(r"(SyntaxError|IndentationError):\s*(.*)")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _combine_output(result: subprocess.CompletedProcess[str]) -> str:
    blocks = [result.stdout, result.stderr]
    return "\n".join(block for block in blocks if block)


def _relative_path(path_text: str) -> str:
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return to_repo_relative(path)
    except Exception:
        return path_text


def _extract_error_location(output: str) -> dict[str, Any] | None:
    if not output:
        return None
    error_file = None
    error_line = None
    error_code = None
    lines = output.splitlines()
    for idx, line in enumerate(lines):
        match = ERROR_COMPILE_RE.search(line)
        if match:
            error_file = _relative_path(match.group(1))
            continue
        file_match = FILE_LINE_RE.search(line)
        if file_match:
            error_file = _relative_path(file_match.group(1))
            try:
                error_line = int(file_match.group(2))
            except ValueError:
                error_line = None
            if idx + 1 < len(lines):
                error_code = lines[idx + 1].strip()
            break
    if not error_file:
        return None
    return {"file": error_file, "line": error_line, "code": error_code}


def _extract_exception_summary(output: str) -> str | None:
    if not output:
        return None
    for line in reversed(output.splitlines()):
        text = line.strip()
        if not text:
            continue
        match = SYNTAX_RE.search(text)
        if match:
            return f"{match.group(1)}: {match.group(2)}".strip()
        if "Error compiling" in text:
            return text
    return output.splitlines()[-1].strip() if output.splitlines() else None


def _prepare_force_fail(targets: Iterable[str], force_fail: bool) -> Path | None:
    if not force_fail:
        return None
    tools_dir = ROOT / "tools"
    temp_path = tools_dir / "_pr39_compile_fail_tmp.py"
    temp_path.write_text("def broken(:\n    return 1\n", encoding="utf-8")
    return temp_path


def _cleanup_force_fail(path: Path | None) -> None:
    if path and path.exists():
        path.unlink(missing_ok=True)


def run_compile_check(
    targets: Iterable[str] | None = None,
    artifacts_dir: Path | None = None,
    force_fail_env: str = "PR39_FORCE_FAIL",
) -> dict[str, Any]:
    targets = list(targets or DEFAULT_TARGETS)
    artifacts_dir = artifacts_dir or (ROOT / "artifacts")
    log_path = artifacts_dir / LOG_FILENAME
    result_path = artifacts_dir / RESULT_FILENAME

    force_fail = os.environ.get(force_fail_env) == "1"
    forced_file = _prepare_force_fail(targets, force_fail)

    cmd = [sys.executable, "-m", "compileall", "-q", *targets]
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        combined = _combine_output(result)
    finally:
        _cleanup_force_fail(forced_file)

    _write_text(log_path, combined)

    status = "PASS" if result.returncode == 0 else "FAIL"
    error_location = _extract_error_location(combined) if status == "FAIL" else None
    exception_summary = _extract_exception_summary(combined) if status == "FAIL" else None

    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "returncode": result.returncode,
        "command": cmd,
        "targets": targets,
        "log_path": to_repo_relative(log_path),
        "exception_summary": exception_summary,
        "error_location": error_location,
        "force_fail": force_fail,
    }
    _write_json(result_path, payload)
    return payload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compileall gate for tools")
    parser.add_argument(
        "--targets",
        nargs="+",
        default=DEFAULT_TARGETS,
        help="Targets to compile (default: tools)",
    )
    parser.add_argument(
        "--artifacts-dir",
        default=str(ROOT / "artifacts"),
        help="Artifacts output directory",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    artifacts_dir = Path(args.artifacts_dir)
    payload = run_compile_check(targets=args.targets, artifacts_dir=artifacts_dir)

    if payload.get("status") != "PASS":
        print("COMPILE_CHECK_FAIL")
        summary = payload.get("exception_summary")
        if summary:
            print(f" - {summary}")
        return 1

    print("COMPILE_CHECK_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
