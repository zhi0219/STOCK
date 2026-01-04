from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_MARKERS = [
    "SAFE_PUSH_START",
    "SAFE_PUSH_GATE",
    "SAFE_PUSH_SUMMARY",
    "READY_TO_MERGE",
    "SAFE_PUSH_END",
]

REQUIRED_COMMAND_SNIPPETS = [
    "-m tools.compile_check",
    "-m tools.git_health report",
    "-m tools.verify_foundation",
    "-m tools.verify_consistency",
]


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe push contract check.")
    parser.add_argument(
        "--script",
        type=Path,
        default=Path("scripts/safe_push_v1.ps1"),
        help="Safe push script to validate.",
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

    for snippet in REQUIRED_COMMAND_SNIPPETS:
        if snippet not in content:
            errors.append(f"missing_command:{snippet}")

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
    _write_json(artifacts_dir / "safe_push_contract_result.json", payload)
    (artifacts_dir / "safe_push_contract.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("SAFE_PUSH_CONTRACT_START")
    print(f"SAFE_PUSH_CONTRACT_SUMMARY|status={status}|errors={len(errors)}")
    print("SAFE_PUSH_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
