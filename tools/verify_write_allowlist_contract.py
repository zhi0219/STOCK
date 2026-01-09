from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WRITE_COMMANDS = (
    "New-Item",
    "Set-Content",
    "Add-Content",
    "Out-File",
    "Copy-Item",
    "Move-Item",
    "Remove-Item",
)

ALLOWED_TOKENS = (
    "$ArtifactsDir",
    "$runDir",
    "$safePullDir",
    "$repoDoctorDir",
    "$dailyOutPath",
    "$dailyErrPath",
    "$StepDir",
    "$stdoutPath",
    "$stderrPath",
)

TARGET_SCRIPTS = [
    Path("scripts/win_daily_green_v1.ps1"),
]

SUMMARY_MARKER = "WRITE_ALLOWLIST_CONTRACT_SUMMARY"


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    for idx, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:idx]
    return line


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write allowlist contract for daily scripts.")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory to write results.",
    )
    return parser.parse_args(argv)


def _line_has_write_command(line: str) -> bool:
    for cmd in WRITE_COMMANDS:
        if re.search(rf"\\b{re.escape(cmd)}\\b", line, re.IGNORECASE):
            return True
    return False


def _line_has_allowed_token(line: str) -> bool:
    return any(token in line for token in ALLOWED_TOKENS) or "artifacts" in line.lower()


def _check_script(script_path: Path) -> list[str]:
    errors: list[str] = []
    if not script_path.exists():
        errors.append(f"missing_script:{script_path.as_posix()}")
        return errors
    content = script_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for idx, raw_line in enumerate(content, start=1):
        line = _strip_inline_comment(raw_line)
        if not line.strip():
            continue
        if not _line_has_write_command(line):
            continue
        if not _line_has_allowed_token(line):
            errors.append(
                f"write_outside_allowlist:{script_path.as_posix()}:{idx}:{raw_line.strip()}"
            )
    return errors


def _check_contract() -> tuple[str, list[str]]:
    errors: list[str] = []
    for script in TARGET_SCRIPTS:
        errors.extend(_check_script(script))
    status = "PASS" if not errors else "FAIL"
    return status, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    status, errors = _check_contract()
    payload = {
        "status": status,
        "errors": errors,
        "scripts": [path.as_posix() for path in TARGET_SCRIPTS],
        "ts_utc": _ts_utc(),
    }

    artifacts_dir = args.artifacts_dir
    _write_json(artifacts_dir / "verify_write_allowlist_contract.json", payload)
    (artifacts_dir / "verify_write_allowlist_contract.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("WRITE_ALLOWLIST_CONTRACT_START")
    print(f"{SUMMARY_MARKER}|status={status}|errors={len(errors)}")
    print("WRITE_ALLOWLIST_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
