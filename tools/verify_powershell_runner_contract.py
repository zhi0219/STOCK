from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_MARKERS = [
    "PS_RUN_START",
    "PS_RUN_SUMMARY",
    "PS_RUN_END",
]

REQUIRED_ARTIFACTS = [
    "ps_run_summary.json",
    "ps_run_stdout.txt",
    "ps_run_stderr.txt",
    "ps_run_markers.txt",
]

REQUIRED_HELPER_SNIPPETS = [
    "Invoke-PsRunner",
    "SystemDirectory",
    "ArgumentList",
]

SCRIPTS_TO_CHECK = [
    Path("scripts/safe_push_v1.ps1"),
    Path("scripts/run_ui_windows.ps1"),
    Path("scripts/run_local_model_edits_v1.ps1"),
]


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PowerShell runner contract check.")
    parser.add_argument(
        "--runner",
        type=Path,
        default=Path("scripts/powershell_runner.ps1"),
        help="PowerShell runner helper script to validate.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory to write results.",
    )
    return parser.parse_args(argv)


def _check_contract(runner_path: Path) -> tuple[str, list[str]]:
    errors: list[str] = []
    if not runner_path.exists():
        return "FAIL", ["missing_runner"]

    content = runner_path.read_text(encoding="utf-8", errors="replace")
    for marker in REQUIRED_MARKERS:
        if marker not in content:
            errors.append(f"missing_marker:{marker}")

    for artifact in REQUIRED_ARTIFACTS:
        if artifact not in content:
            errors.append(f"missing_artifact:{artifact}")

    for snippet in REQUIRED_HELPER_SNIPPETS:
        if snippet not in content:
            errors.append(f"missing_helper_snippet:{snippet}")

    for script_path in SCRIPTS_TO_CHECK:
        if not script_path.exists():
            errors.append(f"missing_script:{script_path.as_posix()}")
            continue
        script_content = script_path.read_text(encoding="utf-8", errors="replace")
        if "powershell_runner.ps1" not in script_content:
            errors.append(f"missing_helper_import:{script_path.as_posix()}")
        if "Invoke-PsRunner" not in script_content:
            errors.append(f"missing_helper_usage:{script_path.as_posix()}")

    status = "PASS" if not errors else "FAIL"
    return status, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    status, errors = _check_contract(args.runner)
    payload = {
        "status": status,
        "errors": errors,
        "runner": args.runner.as_posix(),
        "ts_utc": _ts_utc(),
    }

    artifacts_dir = args.artifacts_dir
    _write_json(artifacts_dir / "powershell_runner_contract_result.json", payload)
    (artifacts_dir / "powershell_runner_contract.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("POWERSHELL_RUNNER_CONTRACT_START")
    print(f"POWERSHELL_RUNNER_CONTRACT_SUMMARY|status={status}|errors={len(errors)}")
    print("POWERSHELL_RUNNER_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
