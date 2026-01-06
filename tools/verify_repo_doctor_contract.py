from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REQUIRED_MARKERS = [
    "REPO_DOCTOR_START",
    "REPO_DOCTOR_STEP",
    "REPO_DOCTOR_SUMMARY",
    "REPO_DOCTOR_END",
]

REQUIRED_COMMAND_PATTERNS = [
    r"tools\.inventory_repo",
    r"--write-docs",
    r"tools\.verify_pr_ready",
    r"git status --porcelain",
]

DISALLOWED_COMMAND_PATTERNS = [
    r"git push",
    r"git merge",
    r"git pull",
    r"gh pr merge",
]

SUMMARY_MARKER = "REPO_DOCTOR_CONTRACT_SUMMARY"


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repo doctor contract check.")
    parser.add_argument(
        "--script",
        type=Path,
        default=Path("scripts/repo_doctor_v1.ps1"),
        help="Repo doctor script to validate.",
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

    summary_lines = [line for line in lines if line.startswith("REPO_DOCTOR_SUMMARY")]
    if summary_lines:
        summary_line = summary_lines[-1]
        for token in ("status=", "failed_step=", "next="):
            if token not in summary_line:
                errors.append(f"summary_missing_token:{token}")
    else:
        errors.append("summary_line_missing")

    return not errors, errors


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
    _write_json(artifacts_dir / "verify_repo_doctor_contract.json", payload)
    (artifacts_dir / "verify_repo_doctor_contract.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("REPO_DOCTOR_CONTRACT_START")
    print(f"{SUMMARY_MARKER}|status={status}|errors={len(errors)}")
    print("REPO_DOCTOR_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
