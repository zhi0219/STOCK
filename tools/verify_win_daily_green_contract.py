from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REQUIRED_MARKERS = [
    "DAILY_GREEN_START",
    "DAILY_GREEN_STEP",
    "DAILY_GREEN_SUMMARY",
    "DAILY_GREEN_END",
]

REQUIRED_COMMAND_PATTERNS = [
    r"safe_pull_v1\.ps1",
    r"repo_doctor_v1\.ps1",
    r"-WriteDocs",
    r"-WriteDocs[\s\S]*?\"NO\"",
    r"-AllowStash",
    r"-RequireClean",
    r"git status --porcelain",
    r"Start-Process",
    r"-RedirectStandardOutput",
    r"-RedirectStandardError",
    r"daily_green_out\.txt",
    r"daily_green_err\.txt",
    r"safe_pull",
    r"repo_doctor",
]

DISALLOWED_COMMAND_PATTERNS = [
    r"Out-Host",
    r"Write-Error",
]

SUMMARY_MARKER = "WIN_DAILY_GREEN_CONTRACT_SUMMARY"
CURRENT_CONTRACT_VERSION = 2


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows daily green contract check.")
    parser.add_argument(
        "--script",
        type=Path,
        default=Path("scripts/win_daily_green_v1.ps1"),
        help="Daily green script to validate.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory to write results.",
    )
    return parser.parse_args(argv)


def _extract_contract_version(content: str) -> int | None:
    match = re.search(r"contractVersion\s*=\s*(\d+)", content)
    if not match:
        return None
    return int(match.group(1))


def _detect_safe_pull_pattern(content: str) -> str:
    mode_match = re.search(r"-Mode\"?[\s,]+\"?([a-z_]+)\"?", content)
    if mode_match:
        return f"mode:{mode_match.group(1)}"
    if re.search(r"-DryRun", content):
        return "dryrun"
    return "missing"


def _check_contract(script_path: Path) -> tuple[str, list[str]]:
    errors: list[str] = []
    if not script_path.exists():
        errors.append("missing_script")
        return "FAIL", errors

    content = script_path.read_text(encoding="utf-8", errors="replace")
    contract_version = _extract_contract_version(content)
    supported_versions = {CURRENT_CONTRACT_VERSION, CURRENT_CONTRACT_VERSION - 1}
    if contract_version is None:
        errors.append("missing_contract_version")
    elif contract_version not in supported_versions:
        errors.append("unsupported_contract_version")

    safe_pull_pattern = _detect_safe_pull_pattern(content)
    if safe_pull_pattern == "missing":
        errors.append("missing_safe_pull_mode_pattern")

    for marker in REQUIRED_MARKERS:
        if marker not in content:
            errors.append(f"missing_marker:{marker}")

    for pattern in REQUIRED_COMMAND_PATTERNS:
        if not re.search(pattern, content):
            errors.append(f"missing_command_pattern:{pattern}")

    for pattern in DISALLOWED_COMMAND_PATTERNS:
        if re.search(pattern, content):
            errors.append(f"disallowed_command_pattern:{pattern}")

    status = "PASS" if not errors else "FAIL"
    return status, errors


def validate_marker_output(output: str | Iterable[str]) -> tuple[bool, list[str]]:
    if isinstance(output, str):
        lines = [line.strip() for line in output.splitlines() if line.strip()]
    else:
        lines = [line.strip() for line in output if line.strip()]

    errors: list[str] = []
    required_found = {marker: False for marker in REQUIRED_MARKERS}

    for line in lines:
        for marker in REQUIRED_MARKERS:
            if line.startswith(marker):
                required_found[marker] = True

    for marker, present in required_found.items():
        if not present:
            errors.append(f"missing_output_marker:{marker}")

    summary_lines = [line for line in lines if line.startswith("DAILY_GREEN_SUMMARY")]
    if summary_lines:
        summary_line = summary_lines[-1]
        for token in ("status=", "failed_step=", "next=", "run_dir=", "contract_version="):
            if token not in summary_line:
                errors.append(f"summary_missing_token:{token}")
    else:
        errors.append("summary_line_missing")

    return not errors, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    status, errors = _check_contract(args.script)
    content = args.script.read_text(encoding="utf-8", errors="replace")
    contract_version = _extract_contract_version(content)
    safe_pull_pattern = _detect_safe_pull_pattern(content)
    payload = {
        "status": status,
        "errors": errors,
        "script": args.script.as_posix(),
        "contract_version": contract_version,
        "safe_pull_pattern": safe_pull_pattern,
        "ts_utc": _ts_utc(),
    }

    artifacts_dir = args.artifacts_dir
    _write_json(artifacts_dir / "verify_win_daily_green_contract.json", payload)
    (artifacts_dir / "verify_win_daily_green_contract.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("WIN_DAILY_GREEN_CONTRACT_START")
    print(
        f"{SUMMARY_MARKER}|status={status}|errors={len(errors)}"
        f"|contract_version={contract_version}|safe_pull_pattern={safe_pull_pattern}"
    )
    print("WIN_DAILY_GREEN_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
