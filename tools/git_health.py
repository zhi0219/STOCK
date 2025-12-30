from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools import repo_hygiene
from tools.migrate_policy_registry import migrate_policy_registry
from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
ARTIFACT_REPORT = ROOT / "artifacts" / "git_health_report.json"
ARTIFACT_FIX = ROOT / "artifacts" / "git_health_fix_result.json"
BACKUP_ROOT = ROOT / "_local_backup"
LEGACY_POLICY_PATH = ROOT / "Logs" / "policy_registry.json"
RUNTIME_POLICY_PATH = ROOT / "Logs" / "runtime" / "policy_registry.json"

ALLOWLIST_RESET = {"tools/ui_app.py"}
SOURCE_EXTENSIONS = {".py", ".sh", ".md", ".ps1"}


@dataclass
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_git(cmd: list[str]) -> GitCommandResult:
    result = subprocess.run(
        ["git", *cmd],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return GitCommandResult(result.returncode, result.stdout, result.stderr)


def _git_available() -> tuple[bool, str | None]:
    result = _run_git(["rev-parse", "--is-inside-work-tree"])
    if result.returncode != 0:
        return False, result.stderr.strip() or "git unavailable"
    return True, None


def _collect_ls_files() -> list[str]:
    result = _run_git(["ls-files", "-v"])
    if result.returncode != 0:
        return []
    return [line.rstrip("\n") for line in result.stdout.splitlines() if line.strip()]


def _collect_status_lines() -> list[str]:
    result = _run_git(["status", "--porcelain", "--untracked-files=all"])
    if result.returncode != 0:
        return []
    return [line.rstrip("\n") for line in result.stdout.splitlines() if line.strip()]


def _normalize_path(text: str) -> str:
    return text.replace("\\", "/")


def _parse_status_path(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return _normalize_path(path)


def _scan_conflict_markers(paths: Iterable[Path]) -> list[dict[str, Any]]:
    markers = ("<<<<<<<", "=======", ">>>>>>>")
    hits: list[dict[str, Any]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if any(marker in line for marker in markers):
                hits.append(
                    {"path": to_repo_relative(path), "line": idx, "line_text": line.strip()}
                )
                break
    return hits


def _ls_files_path(line: str) -> str:
    if not line:
        return ""
    if line[0] == " ":
        return line[1:].strip()
    if len(line) > 1 and line[1] == " ":
        return line[2:].strip()
    return line[1:].strip()


def _tracked_source_paths() -> list[Path]:
    paths = []
    for line in _collect_ls_files():
        path_text = _ls_files_path(line)
        if not path_text:
            continue
        path = ROOT / path_text
        if path.suffix.lower() in SOURCE_EXTENSIONS:
            paths.append(path)
    return paths


def _collect_flagged_files(prefixes: set[str]) -> list[str]:
    flagged: list[str] = []
    for line in _collect_ls_files():
        if not line:
            continue
        flag = line[0]
        if flag in prefixes:
            path = _ls_files_path(line)
            flagged.append(_normalize_path(path))
    return flagged


def build_report() -> dict[str, Any]:
    git_ok, git_error = _git_available()
    skip_worktree = _collect_flagged_files({"S", "s"}) if git_ok else []
    assume_unchanged = _collect_flagged_files({"H", "h"}) if git_ok else []
    status_lines = _collect_status_lines() if git_ok else []

    tracked_modified: list[str] = []
    tracked_runtime_modified: list[str] = []
    for line in status_lines:
        if line.startswith("?? ") or line.startswith("!! "):
            continue
        path = _parse_status_path(line)
        tracked_modified.append(path)
        if repo_hygiene.is_runtime_path(path):
            tracked_runtime_modified.append(path)

    conflict_hits = _scan_conflict_markers(_tracked_source_paths()) if git_ok else []
    legacy_present = LEGACY_POLICY_PATH.exists()

    status = "PASS"
    reasons = []
    if skip_worktree or assume_unchanged:
        status = "FAIL"
        reasons.append("hidden_dirty_flags")
    if conflict_hits:
        status = "FAIL"
        reasons.append("conflict_markers")
    if legacy_present or tracked_runtime_modified:
        status = "FAIL"
        reasons.append("runtime_blockers")

    payload = {
        "schema_version": 1,
        "ts_utc": _iso_now(),
        "status": status,
        "reasons": reasons,
        "git_available": git_ok,
        "git_error": git_error,
        "skip_worktree": skip_worktree,
        "assume_unchanged": assume_unchanged,
        "tracked_modified": tracked_modified,
        "tracked_runtime_modified": tracked_runtime_modified,
        "legacy_policy_registry_present": legacy_present,
        "legacy_policy_registry_path": to_repo_relative(LEGACY_POLICY_PATH),
        "runtime_policy_registry_path": to_repo_relative(RUNTIME_POLICY_PATH),
        "conflict_markers": conflict_hits,
    }
    ARTIFACT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_REPORT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _write_patch(path: Path) -> bool:
    result = _run_git(["diff"])
    if result.returncode != 0 or not result.stdout.strip():
        return False
    path.write_text(result.stdout, encoding="utf-8")
    return True


def _backup_file(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    target = backup_dir / path.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.read_bytes())
    return target


def _reset_paths(paths: list[str]) -> None:
    if not paths:
        return
    _run_git(["checkout", "--", *paths])


def fix_safe() -> dict[str, Any]:
    report = build_report()
    backup_dir = BACKUP_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)

    if not report.get("git_available"):
        payload = {
            "schema_version": 1,
            "ts_utc": _iso_now(),
            "status": "REFUSED",
            "message": f"Git unavailable: {report.get('git_error')}",
            "backup_dir": to_repo_relative(backup_dir),
            "patch_written": False,
            "runtime_backups": [],
            "flags_removed": [],
            "allowlist_reset": [],
            "blocked_paths": [],
            "migration_result": migrate_policy_registry(),
        }
        ARTIFACT_FIX.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT_FIX.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    patch_written = _write_patch(backup_dir / "tracked_changes.patch")
    runtime_backups: list[str] = []
    for candidate in [LEGACY_POLICY_PATH, RUNTIME_POLICY_PATH]:
        backup_path = _backup_file(candidate, backup_dir)
        if backup_path:
            runtime_backups.append(to_repo_relative(backup_path))

    flagged_removed = []
    for path in report.get("skip_worktree", []):
        if repo_hygiene.is_runtime_path(path):
            continue
        _run_git(["update-index", "--no-skip-worktree", path])
        flagged_removed.append(path)
    for path in report.get("assume_unchanged", []):
        if repo_hygiene.is_runtime_path(path):
            continue
        _run_git(["update-index", "--no-assume-unchanged", path])
        flagged_removed.append(path)

    migration_result = migrate_policy_registry()

    status_lines = _collect_status_lines()
    tracked_modified: list[str] = []
    for line in status_lines:
        if line.startswith("?? ") or line.startswith("!! "):
            continue
        path = _parse_status_path(line)
        if repo_hygiene.is_runtime_path(path):
            continue
        tracked_modified.append(path)

    reset_paths: list[str] = []
    blocked_paths: list[str] = []
    for path in tracked_modified:
        if path in ALLOWLIST_RESET:
            reset_paths.append(path)
        else:
            blocked_paths.append(path)

    for path in reset_paths:
        _backup_file(ROOT / path, backup_dir)

    if reset_paths:
        _reset_paths(reset_paths)

    result_status = "PASS"
    message = "Git health safe fix applied."
    if blocked_paths:
        result_status = "REFUSED"
        message = "Tracked source changes require review before reset."

    payload = {
        "schema_version": 1,
        "ts_utc": _iso_now(),
        "status": result_status,
        "message": message,
        "backup_dir": to_repo_relative(backup_dir),
        "patch_written": patch_written,
        "runtime_backups": runtime_backups,
        "flags_removed": sorted(set(flagged_removed)),
        "allowlist_reset": sorted(reset_paths),
        "blocked_paths": blocked_paths,
        "migration_result": migration_result,
    }
    ARTIFACT_FIX.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_FIX.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _print_summary(status: str, flags_removed: int, backup_dir: str) -> None:
    print("GIT_HEALTH_START")
    print(f"GIT_HEALTH_SUMMARY|status={status}|flags_removed={flags_removed}|backups_written={backup_dir}")
    print("GIT_HEALTH_END")


def main() -> int:
    parser = argparse.ArgumentParser(description="Git hidden-dirty health checks.")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--report", action="store_true", help="Write git health report.")
    group.add_argument("--fix-safe", action="store_true", help="Apply safe fixes.")
    args = parser.parse_args()

    if args.fix_safe:
        result = fix_safe()
        _print_summary(result.get("status", "UNKNOWN"), len(result.get("flags_removed", [])), result.get("backup_dir", ""))
        return 0 if result.get("status") == "PASS" else 1

    report = build_report()
    _print_summary(report.get("status", "UNKNOWN"), 0, "")
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
