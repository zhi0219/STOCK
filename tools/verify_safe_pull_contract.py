from __future__ import annotations

import argparse
import json
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
]

REQUIRED_ARTIFACTS = [
    "safe_pull_run.json",
    "safe_pull_summary.json",
    "safe_pull_summary.txt",
    "safe_pull_out.txt",
    "safe_pull_err.txt",
    "safe_pull_markers.txt",
    "git_status_before.txt",
    "git_status_after.txt",
    "git_porcelain_before.txt",
    "git_porcelain_after.txt",
    "git_rev_before.txt",
    "git_rev_after.txt",
    "config_snapshot.txt",
    "safe_pull_precheck_head.txt",
    "safe_pull_precheck_upstream.txt",
    "safe_pull_precheck_ahead_behind.txt",
    "git_status_porcelain.out.txt",
    "git_status_porcelain.err.txt",
    "git_fetch.out.txt",
    "git_fetch.err.txt",
    "git_checkout_main.out.txt",
    "git_checkout_main.err.txt",
    "git_pull_ff_only.out.txt",
    "git_pull_ff_only.err.txt",
    "git_rev_parse_head.out.txt",
    "git_rev_parse_head.err.txt",
    "git_rev_parse_origin_main.out.txt",
    "git_rev_parse_origin_main.err.txt",
]


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify safe pull artifacts contract.")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory to write results.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Directory containing safe pull artifacts (defaults to artifacts dir).",
    )
    return parser.parse_args(argv)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _read_invocation(input_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    invocation_path = input_dir / "safe_pull_invocation.json"
    if not invocation_path.exists():
        return None, errors
    try:
        return json.loads(invocation_path.read_text(encoding="utf-8")), errors
    except json.JSONDecodeError:
        errors.append("invocation_json_invalid")
        return None, errors


def _parse_markers(lines: list[str]) -> tuple[list[str], dict[str, dict[str, str]]]:
    errors: list[str] = []
    parsed: dict[str, dict[str, str]] = {}
    for line in lines:
        if "|" not in line:
            if line in {"SAFE_PULL_END"}:
                continue
            errors.append(f"marker_missing_delimiter:{line}")
            continue
        parts = line.split("|")
        if not parts[0]:
            errors.append(f"marker_missing_prefix:{line}")
        payload: dict[str, str] = {}
        for token in parts[1:]:
            if "=" not in token:
                errors.append(f"marker_missing_key_value:{line}")
                break
            key, value = token.split("=", 1)
            payload[key] = value
        parsed[parts[0]] = payload
    return errors, parsed


def _validate_summary(payload: dict[str, Any]) -> list[str]:
    required_keys = [
        "status",
        "mode",
        "next",
        "dry_run",
        "artifacts_dir",
        "reason",
        "phase",
        "run_id",
        "evidence_artifact",
    ]
    errors: list[str] = []
    for key in required_keys:
        if key not in payload:
            errors.append(f"summary_missing_key:{key}")
    if payload.get("status") not in {"PASS", "FAIL", "DEGRADED"}:
        errors.append("summary_invalid_status")
    return errors


def _validate_invariants(
    input_dir: Path, summary: dict[str, Any], markers: dict[str, dict[str, str]]
) -> list[str]:
    errors: list[str] = []
    before = _read_text(input_dir / "git_porcelain_before.txt")
    after = _read_text(input_dir / "git_porcelain_after.txt")
    if summary.get("status") == "PASS":
        if after.strip():
            errors.append("postcheck_porcelain_not_empty")
    if summary.get("dry_run"):
        if before != after:
            errors.append("dry_run_porcelain_changed")
    precheck = markers.get("SAFE_PULL_PRECHECK", {})
    if precheck.get("detached") == "0" and not precheck.get("branch"):
        errors.append("precheck_branch_blank_not_detached")
    if summary.get("status") == "FAIL" and summary.get("reason") == "internal_exception":
        if not (input_dir / "safe_pull_exception.json").exists():
            errors.append("exception_missing_json")
        if not (input_dir / "safe_pull_exception.txt").exists():
            errors.append("exception_missing_txt")
        if "SAFE_PULL_EXCEPTION" not in markers:
            errors.append("missing_marker:SAFE_PULL_EXCEPTION")
    if summary.get("dry_run") and precheck:
        if (
            precheck.get("porcelain") == "0"
            and precheck.get("untracked") == "0"
            and precheck.get("diverged") == "0"
            and precheck.get("detached") == "0"
            and summary.get("status") != "PASS"
        ):
            errors.append("dry_run_clean_should_pass")
    return errors


def _validate_summary_text(summary_text: str) -> list[str]:
    errors: list[str] = []
    if not summary_text:
        return ["summary_txt_missing"]
    first_line = summary_text.splitlines()[0] if summary_text else ""
    if not first_line.startswith("SAFE_PULL_SUMMARY|"):
        errors.append("summary_txt_missing_marker")
        return errors
    required_tokens = ["repo=", "evidence_dir=", "head=", "origin_main=", "ts_utc="]
    for token in required_tokens:
        if token not in first_line:
            errors.append(f"summary_txt_missing_token:{token}")
    return errors


def _check_contract(input_dir: Path) -> tuple[str, list[str]]:
    errors: list[str] = []
    missing_artifacts = [
        artifact for artifact in REQUIRED_ARTIFACTS if not (input_dir / artifact).exists()
    ]
    invocation_payload, invocation_errors = _read_invocation(input_dir)
    errors.extend(invocation_errors)
    if missing_artifacts:
        exit_code = None
        if invocation_payload is not None:
            exit_code = invocation_payload.get("exit_code")
        if exit_code not in (None, 0):
            errors.append("upstream_failed")
            errors.append(f"upstream_failed_exit_code:{exit_code}")
            stdout_path = invocation_payload.get("stdout_path") if invocation_payload else None
            stderr_path = invocation_payload.get("stderr_path") if invocation_payload else None
            if stdout_path:
                errors.append(f"upstream_failed_stdout:{stdout_path}")
            if stderr_path:
                errors.append(f"upstream_failed_stderr:{stderr_path}")
            for artifact in missing_artifacts:
                errors.append(f"upstream_failed_missing_artifact:{artifact}")
        else:
            for artifact in missing_artifacts:
                errors.append(f"missing_artifact:{artifact}")

    markers_path = input_dir / "safe_pull_markers.txt"
    marker_lines = [
        line.strip()
        for line in _read_text(markers_path).splitlines()
        if line.strip()
    ]
    marker_payloads: dict[str, dict[str, str]] = {}
    if marker_lines:
        marker_errors, marker_payloads = _parse_markers(marker_lines)
        errors.extend(marker_errors)
    for marker in REQUIRED_MARKERS:
        if not any(line.startswith(marker) for line in marker_lines):
            errors.append(f"missing_marker:{marker}")

    summary_path = input_dir / "safe_pull_summary.json"
    summary_payload: dict[str, Any] = {}
    if summary_path.exists():
        try:
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append("summary_json_invalid")
    else:
        summary_payload = {}

    errors.extend(_validate_summary(summary_payload))
    errors.extend(_validate_invariants(input_dir, summary_payload, marker_payloads))
    errors.extend(_validate_summary_text(_read_text(input_dir / "safe_pull_summary.txt")))

    status = "PASS" if not errors else "FAIL"
    return status, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    input_dir = args.input_dir if args.input_dir is not None else args.artifacts_dir
    status, errors = _check_contract(input_dir)

    payload = {
        "status": status,
        "errors": errors,
        "input_dir": input_dir.as_posix(),
        "ts_utc": _ts_utc(),
    }

    artifacts_dir = args.artifacts_dir
    _write_json(artifacts_dir / "verify_safe_pull_contract.json", payload)
    (artifacts_dir / "verify_safe_pull_contract.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("SAFE_PULL_CONTRACT_START")
    print(f"SAFE_PULL_CONTRACT_SUMMARY|status={status}|errors={len(errors)}")
    print("SAFE_PULL_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
