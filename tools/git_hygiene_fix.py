from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools import repo_hygiene
from tools.paths import repo_root, to_repo_relative
from tools.verify_repo_hygiene import REQUIRED_RULES

ROOT = repo_root()
PLAN_PATH = ROOT / "artifacts" / "git_hygiene_fix_plan.json"
RESULT_PATH = ROOT / "artifacts" / "git_hygiene_fix_result.json"
MAX_STATUS_LINES = 200


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(lines: list[str], limit: int = MAX_STATUS_LINES) -> tuple[list[str], bool]:
    if len(lines) <= limit:
        return list(lines), False
    return list(lines[:limit]), True


def _read_gitignore() -> str:
    path = ROOT / ".gitignore"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _missing_rules(content: str) -> list[str]:
    return [rule for rule in REQUIRED_RULES if rule not in content]


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
    return entries


def build_plan(max_status_lines: int = MAX_STATUS_LINES) -> dict[str, Any]:
    status_lines, error = repo_hygiene.git_status_porcelain(include_ignored=True)
    git_available = error is None
    summary = repo_hygiene.scan_repo() if git_available else {"tracked_modified": [], "untracked": [], "ignored": []}
    entries = _collect_entries(summary)
    safe_entries = [entry for entry in entries if entry["classification"] == "RUNTIME_ARTIFACT"]
    unknown_entries = [entry for entry in entries if entry["classification"] != "RUNTIME_ARTIFACT"]

    safe_tracked = [entry["path"] for entry in safe_entries if entry["bucket"] == "tracked_modified"]
    safe_untracked = [entry["path"] for entry in safe_entries if entry["bucket"] == "untracked"]
    safe_ignored = [entry["path"] for entry in safe_entries if entry["bucket"] == "ignored"]

    bounded_lines, truncated = _bounded(status_lines, max_status_lines)
    gitignore_content = _read_gitignore()
    missing_rules = _missing_rules(gitignore_content)

    return {
        "schema_version": 1,
        "ts_utc": _iso_now(),
        "action_id": "FIX_GIT_RED_SAFE",
        "repo_root_rel": ".",
        "git_available": git_available,
        "git_error": error,
        "git_status_before": {
            "lines": bounded_lines,
            "truncated": truncated,
            "total_lines": len(status_lines),
        },
        "safe_paths": {
            "restore_tracked": safe_tracked,
            "remove_untracked": safe_untracked,
            "remove_ignored": safe_ignored,
        },
        "unknown_paths": unknown_entries,
        "gitignore_missing_rules": missing_rules,
    }


def _update_gitignore(missing_rules: list[str]) -> list[str]:
    if not missing_rules:
        return []
    path = ROOT / ".gitignore"
    existing = _read_gitignore()
    lines = existing.splitlines()
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(missing_rules)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return [to_repo_relative(path)]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_plan(plan: dict[str, Any], path: Path = PLAN_PATH) -> None:
    _write_json(path, plan)


def apply_fix(plan: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    status_lines, error = repo_hygiene.git_status_porcelain(include_ignored=True)
    bounded_lines, truncated = _bounded(status_lines)
    result: dict[str, Any] = {
        "schema_version": 1,
        "ts_utc": _iso_now(),
        "action_id": plan.get("action_id", "FIX_GIT_RED_SAFE"),
        "repo_root_rel": ".",
        "dry_run": dry_run,
        "status": "UNKNOWN",
        "message": "",
        "git_available": error is None,
        "git_error": error,
        "git_status_before": {
            "lines": bounded_lines,
            "truncated": truncated,
            "total_lines": len(status_lines),
        },
        "git_status_after": {},
        "changes_made": [],
        "unknown_paths": plan.get("unknown_paths", []),
    }

    if error is not None:
        result["status"] = "REFUSED"
        result["message"] = f"Git unavailable: {error}"
        return result

    if dry_run:
        result["status"] = "DRY_RUN"
        result["message"] = "Dry run only; no mutations executed."
        result["git_status_after"] = result["git_status_before"]
        return result

    safe_paths = plan.get("safe_paths", {}) if isinstance(plan.get("safe_paths"), dict) else {}
    tracked = [str(path) for path in safe_paths.get("restore_tracked", []) if path]
    untracked = [str(path) for path in safe_paths.get("remove_untracked", []) if path]
    ignored = [str(path) for path in safe_paths.get("remove_ignored", []) if path]

    repo_hygiene.restore_tracked(tracked)
    repo_hygiene.remove_runtime_paths(untracked + ignored, repo_hygiene.safe_delete_roots())

    gitignore_missing = plan.get("gitignore_missing_rules", [])
    if isinstance(gitignore_missing, list):
        updated = _update_gitignore([str(rule) for rule in gitignore_missing if rule])
        result["changes_made"].extend(updated)

    if tracked:
        result["changes_made"].extend(tracked)
    if untracked:
        result["changes_made"].extend(untracked)
    if ignored:
        result["changes_made"].extend(ignored)

    after_lines, after_error = repo_hygiene.git_status_porcelain(include_ignored=True)
    after_bounded, after_truncated = _bounded(after_lines)
    result["git_status_after"] = {
        "lines": after_bounded,
        "truncated": after_truncated,
        "total_lines": len(after_lines),
    }
    if after_error is not None:
        result["status"] = "FAIL"
        result["message"] = f"Git status failed after apply: {after_error}"
    else:
        result["status"] = "PASS"
        result["message"] = "Safe git hygiene fix applied."
    return result


def write_result(result: dict[str, Any], path: Path = RESULT_PATH) -> None:
    _write_json(path, result)
