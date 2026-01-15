from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_MARKERS = [
    "SAFE_PULL_RUN_START",
    "SAFE_PULL_START",
    "SAFE_PULL_PRECHECK",
    "SAFE_PULL_LOCK",
    "SAFE_PULL_STASH",
    "SAFE_PULL_FETCH",
    "SAFE_PULL_PULL_FF_ONLY",
    "SAFE_PULL_POSTCHECK",
    "SAFE_PULL_SUMMARY",
    "SAFE_PULL_RUN_END",
    "SAFE_PULL_END",
]

REQUIRED_COMMAND_PATTERNS = [
    r"Invoke-PsRunner",
    r"powershell_runner\.ps1",
    r"pull\.ff=only",
    r"pull\.rebase=false",
    r"pull --ff-only",
    r"stash\",\s*\"push",
    r"status --porcelain",
    r"ls-files -u",
    r"rev-list --left-right --count",
    r"symbolic-ref -q --short HEAD",
    r"rev-parse --abbrev-ref --symbolic-full-name",
]

REQUIRED_STATE_PATTERNS = [
    r"MERGE_HEAD",
    r"CHERRY_PICK_HEAD",
    r"REVERT_HEAD",
    r"rebase-apply",
    r"rebase-merge",
    r"AM",
]
UNSAFE_STDIO_TRIM_PATTERN = r"\(\s*\$stdoutText\s*\+\s*\$stderrText\s*\)\.Trim\(\)"
SAFE_STDIO_CONCAT_PATTERN = r"\[string\]::Concat\(\s*\$stdoutText\s*,\s*\$stderrText\s*\)\.Trim\(\)"
STDOUT_NULL_GUARD_PATTERN = r"if\s*\(\s*\$null\s*-eq\s*\$stdoutText\s*\)\s*\{\s*\$stdoutText\s*=\s*\"\"\s*\}"
STDERR_NULL_GUARD_PATTERN = r"if\s*\(\s*\$null\s*-eq\s*\$stderrText\s*\)\s*\{\s*\$stderrText\s*=\s*\"\"\s*\}"


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe pull contract check.")
    parser.add_argument(
        "--script",
        type=Path,
        default=Path("scripts/safe_pull_v1.ps1"),
        help="Safe pull script to validate.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory to write results.",
    )
    return parser.parse_args(argv)


def _check_contract(script_path: Path) -> tuple[str, list[str]]:
    errors: list[str] = []
    if not script_path.exists():
        errors.append("missing_script")
        return "FAIL", errors

    content = script_path.read_text(encoding="utf-8", errors="replace")

    for marker in REQUIRED_MARKERS:
        if marker not in content:
            errors.append(f"missing_marker:{marker}")

    for pattern in REQUIRED_COMMAND_PATTERNS:
        if not re.search(pattern, content):
            errors.append(f"missing_command_pattern:{pattern}")

    for pattern in REQUIRED_STATE_PATTERNS:
        if not re.search(pattern, content):
            errors.append(f"missing_state_pattern:{pattern}")

    if re.search(UNSAFE_STDIO_TRIM_PATTERN, content):
        errors.append("unsafe_stdio_trim:stdout_stderr_concat")

    if not re.search(SAFE_STDIO_CONCAT_PATTERN, content):
        errors.append("missing_stdio_concat:[string]::Concat")

    if not re.search(STDOUT_NULL_GUARD_PATTERN, content):
        errors.append("missing_stdout_null_guard")

    if not re.search(STDERR_NULL_GUARD_PATTERN, content):
        errors.append("missing_stderr_null_guard")

    status = "PASS" if not errors else "FAIL"
    return status, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    status, errors = _check_contract(args.script)
    payload = {
        "status": status,
        "errors": errors,
        "script": args.script.as_posix(),
        "ts_utc": _ts_utc(),
    }

    artifacts_dir = args.artifacts_dir
    _write_json(artifacts_dir / "safe_pull_contract_result.json", payload)
    (artifacts_dir / "safe_pull_contract.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("SAFE_PULL_CONTRACT_START")
    print(f"SAFE_PULL_CONTRACT_SUMMARY|status={status}|errors={len(errors)}")
    print("SAFE_PULL_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
