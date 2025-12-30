from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools import repo_hygiene
from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "runtime_hygiene_report.json"
RESULT_PATH = ARTIFACTS_DIR / "runtime_hygiene_result.json"
STASH_MESSAGE = "runtime_hygiene_safe_stash"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _collect_entries(summary: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for bucket in ("tracked_modified", "untracked", "ignored"):
        bucket_entries = summary.get(bucket, [])
        if not isinstance(bucket_entries, list):
            continue
        for entry in bucket_entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            classification = entry.get("classification")
            if path:
                entries.append(
                    {
                        "path": str(path),
                        "classification": str(classification) if classification else "UNKNOWN",
                        "bucket": bucket,
                    }
                )
    return sorted(entries, key=lambda item: (item["bucket"], item["path"]))


def _runtime_only(entries: list[dict[str, str]]) -> bool:
    if not entries:
        return False
    return all(entry.get("classification") == "RUNTIME_ARTIFACT" for entry in entries)


def _scan() -> dict[str, Any]:
    status_lines, error = repo_hygiene.git_status_porcelain(include_ignored=True)
    summary = repo_hygiene.scan_repo() if error is None else {}
    entries = _collect_entries(summary)
    runtime_only = _runtime_only(entries)
    payload = {
        "schema_version": 1,
        "status": "PASS" if not entries else "FAIL",
        "git_available": error is None,
        "git_error": error,
        "dirty_entries": entries,
        "runtime_only": runtime_only,
        "status_lines": status_lines,
    }
    _write_json(REPORT_PATH, payload)
    return payload


def _stash_runtime_only(report: dict[str, Any]) -> dict[str, Any]:
    if not report.get("git_available", False):
        return {
            "schema_version": 1,
            "status": "REFUSED",
            "action": "stash",
            "message": "Git unavailable; cannot stash.",
            "changes": [],
        }
    if not report.get("dirty_entries"):
        return {
            "schema_version": 1,
            "status": "NOOP",
            "action": "stash",
            "message": "No dirty entries to stash.",
            "changes": [],
        }
    if not report.get("runtime_only", False):
        return {
            "schema_version": 1,
            "status": "REFUSED",
            "action": "stash",
            "message": "Non-runtime changes detected; refusing to stash.",
            "changes": [],
        }

    result = subprocess.run(
        ["git", "stash", "push", "-u", "-m", STASH_MESSAGE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(block for block in [result.stdout, result.stderr] if block)
    status = "PASS" if result.returncode == 0 else "FAIL"
    return {
        "schema_version": 1,
        "status": status,
        "action": "stash",
        "message": output.strip() if output else "git stash completed",
        "changes": [entry.get("path") for entry in report.get("dirty_entries", [])],
        "stash_message": STASH_MESSAGE,
    }


def _discard_runtime_only(report: dict[str, Any]) -> dict[str, Any]:
    if not report.get("dirty_entries"):
        return {
            "schema_version": 1,
            "status": "NOOP",
            "action": "discard",
            "message": "No dirty entries to discard.",
            "changes": [],
        }
    if not report.get("runtime_only", False):
        return {
            "schema_version": 1,
            "status": "REFUSED",
            "action": "discard",
            "message": "Non-runtime changes detected; refusing to discard.",
            "changes": [],
        }

    repo_hygiene.fix_repo("safe", aggressive=False, confirm_token=None)
    return {
        "schema_version": 1,
        "status": "PASS",
        "action": "discard",
        "message": "Runtime-only artifacts discarded.",
        "changes": [entry.get("path") for entry in report.get("dirty_entries", [])],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runtime-only hygiene helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan runtime-only hygiene status")
    scan_parser.set_defaults(command="scan")

    fix_parser = subparsers.add_parser("fix", help="Fix runtime-only hygiene issues")
    fix_parser.add_argument("--mode", choices=["stash", "discard"], default="stash")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])

    report = _scan()
    if args.command == "scan":
        print("RUNTIME_HYGIENE_SCAN")
        return 0 if report.get("status") == "PASS" else 1

    if args.mode == "stash":
        result = _stash_runtime_only(report)
    else:
        result = _discard_runtime_only(report)

    result["report_path"] = to_repo_relative(REPORT_PATH)
    _write_json(RESULT_PATH, result)
    print("RUNTIME_HYGIENE_FIX")
    return 0 if result.get("status") in {"PASS", "NOOP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
