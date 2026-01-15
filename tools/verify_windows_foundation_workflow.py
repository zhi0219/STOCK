from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SUMMARY_MARKER = "WINDOWS_FOUNDATION_WORKFLOW_SUMMARY"


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate windows_foundation workflow structure."
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(".github/workflows/windows_foundation.yml"),
        help="Workflow file to validate.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory to write results.",
    )
    return parser.parse_args(argv)


def _check_workflow(path: Path) -> tuple[str, list[str]]:
    errors: list[str] = []
    if not path.exists():
        return "FAIL", ["missing_workflow_file"]

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - parser safeguard
        return "FAIL", [f"workflow_yaml_invalid:{exc}"]

    if not isinstance(payload, dict):
        return "FAIL", ["workflow_yaml_not_mapping"]

    jobs = payload.get("jobs")
    if not isinstance(jobs, dict) or "windows-foundation" not in jobs:
        errors.append("missing_windows_foundation_job")
        return "FAIL", errors

    job = jobs["windows-foundation"]
    if not isinstance(job, dict):
        errors.append("windows_foundation_job_invalid")
        return "FAIL", errors

    matrix = job.get("strategy", {}).get("matrix", {})
    include = matrix.get("include") if isinstance(matrix, dict) else None
    if not isinstance(include, list):
        errors.append("matrix_include_missing")
    else:
        matrix_names = {str(entry.get("name")) for entry in include if isinstance(entry, dict)}
        if "ps51" not in matrix_names:
            errors.append("matrix_missing_ps51")
        if "ps7" not in matrix_names:
            errors.append("matrix_missing_ps7")

    steps = job.get("steps")
    if not isinstance(steps, list):
        errors.append("steps_missing")
    else:
        steps_text = json.dumps(steps)
        if "safe_pull_v1.ps1" not in steps_text:
            errors.append("safe_pull_step_missing")
        if "verify_safe_pull_contract" not in steps_text:
            errors.append("verify_safe_pull_contract_step_missing")
        if "upload-artifact" not in steps_text:
            errors.append("upload_artifact_step_missing")

    status = "PASS" if not errors else "FAIL"
    return status, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    status, errors = _check_workflow(args.workflow)
    payload = {
        "status": status,
        "errors": errors,
        "workflow": args.workflow.as_posix(),
        "ts_utc": _ts_utc(),
    }

    _write_json(args.artifacts_dir / "verify_windows_foundation_workflow.json", payload)
    (args.artifacts_dir / "verify_windows_foundation_workflow.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("WINDOWS_FOUNDATION_WORKFLOW_START")
    print(f"{SUMMARY_MARKER}|status={status}|errors={len(errors)}")
    print("WINDOWS_FOUNDATION_WORKFLOW_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
