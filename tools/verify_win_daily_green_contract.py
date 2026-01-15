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

DRY_RUN_CANONICAL_PATTERN = r"-DryRun"
DRY_RUN_LEGACY_PATTERN = r"-Mode\s+dry_run"

DISALLOWED_COMMAND_PATTERNS = [
    r"Out-Host",
    r"Write-Error",
]

SUMMARY_MARKER = "WIN_DAILY_GREEN_CONTRACT_SUMMARY"


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


def _check_contract(script_path: Path) -> tuple[str, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not script_path.exists():
        errors.append("missing_script")
        return "FAIL", errors, warnings

    content = script_path.read_text(encoding="utf-8", errors="replace")

    for marker in REQUIRED_MARKERS:
        if marker not in content:
            errors.append(f"missing_marker:{marker}")

    for pattern in REQUIRED_COMMAND_PATTERNS:
        if not re.search(pattern, content):
            errors.append(f"missing_command_pattern:{pattern}")

    if re.search(DRY_RUN_CANONICAL_PATTERN, content):
        pass
    elif re.search(DRY_RUN_LEGACY_PATTERN, content):
        warnings.append("legacy_dry_run_mode_syntax")
    else:
        errors.append("missing_command_pattern:dry_run_syntax")

    for pattern in DISALLOWED_COMMAND_PATTERNS:
        if re.search(pattern, content):
            errors.append(f"disallowed_command_pattern:{pattern}")

    status = "PASS" if not errors else "FAIL"
    return status, errors, warnings


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
        for token in ("status=", "failed_step=", "next=", "run_dir="):
            if token not in summary_line:
                errors.append(f"summary_missing_token:{token}")
    else:
        errors.append("summary_line_missing")

    return not errors, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    status, errors, warnings = _check_contract(args.script)
    payload = {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "script": args.script.as_posix(),
        "ts_utc": _ts_utc(),
    }

    artifacts_dir = args.artifacts_dir
    _write_json(artifacts_dir / "verify_win_daily_green_contract.json", payload)
    (artifacts_dir / "verify_win_daily_green_contract.txt").write_text(
        "\n".join(errors + warnings) if errors or warnings else "ok",
        encoding="utf-8",
    )

    print("WIN_DAILY_GREEN_CONTRACT_START")
    print(
        f"{SUMMARY_MARKER}|status={status}|errors={len(errors)}|warnings={len(warnings)}"
    )
    print("WIN_DAILY_GREEN_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
