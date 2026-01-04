from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools import repo_hygiene
from tools.paths import repo_root, to_repo_relative


ARTIFACT_JSON_NAME = "git_health_report.json"
ARTIFACT_TEXT_NAME = "git_health_report.txt"


@dataclass(frozen=True)
class HealthReport:
    status: str
    reason: str
    repo_root: str
    dirty_source_files: list[str]
    runtime_untracked: list[str]
    unsafe_untracked: list[str]
    locked_files: list[str]
    git_clean: bool
    suggested_actions: list[str]


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _collect_paths(
    summary: dict[str, Any],
    buckets: tuple[str, ...],
    classification: str | None,
) -> list[str]:
    paths: list[str] = []
    for bucket in buckets:
        entries = summary.get(bucket, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if classification is not None and entry.get("classification") != classification:
                continue
            if classification is None and entry.get("classification") == "RUNTIME_ARTIFACT":
                continue
            path = entry.get("path")
            if path:
                paths.append(str(path))
    return paths


def _git_status_clean(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return False
    return not result.stdout.strip()


def build_report(root: Path) -> HealthReport:
    _, git_error = repo_hygiene.git_status_porcelain(include_ignored=True)
    root_rel = to_repo_relative(root)
    if git_error:
        return HealthReport(
            status="FAIL",
            reason=git_error,
            repo_root=root_rel,
            dirty_source_files=[],
            runtime_untracked=[],
            unsafe_untracked=[],
            locked_files=[],
            git_clean=False,
            suggested_actions=["git status --porcelain"],
        )

    summary = repo_hygiene.scan_repo()
    dirty_source_files = _collect_paths(
        summary,
        ("tracked_modified",),
        None,
    )
    unsafe_untracked = _collect_paths(
        summary,
        ("untracked",),
        None,
    )
    runtime_untracked = _collect_paths(
        summary,
        ("untracked", "ignored"),
        "RUNTIME_ARTIFACT",
    )
    git_clean = _git_status_clean(root)

    if dirty_source_files:
        return HealthReport(
            status="FAIL",
            reason="dirty_source_files",
            repo_root=root_rel,
            dirty_source_files=dirty_source_files,
            runtime_untracked=runtime_untracked,
            unsafe_untracked=unsafe_untracked,
            locked_files=[],
            git_clean=git_clean,
            suggested_actions=["git status --porcelain"],
        )

    if unsafe_untracked:
        return HealthReport(
            status="FAIL",
            reason="unsafe_untracked",
            repo_root=root_rel,
            dirty_source_files=dirty_source_files,
            runtime_untracked=runtime_untracked,
            unsafe_untracked=unsafe_untracked,
            locked_files=[],
            git_clean=git_clean,
            suggested_actions=["git status --porcelain"],
        )

    return HealthReport(
        status="PASS",
        reason="ok",
        repo_root=root_rel,
        dirty_source_files=[],
        runtime_untracked=runtime_untracked,
        unsafe_untracked=[],
        locked_files=[],
        git_clean=git_clean,
        suggested_actions=[],
    )


def apply_safe_fix(root: Path, artifacts_dir: Path) -> HealthReport:
    summary = repo_hygiene.scan_repo()
    tracked_runtime = [
        entry["path"]
        for entry in summary.get("tracked_modified", [])
        if isinstance(entry, dict) and entry.get("classification") == "RUNTIME_ARTIFACT"
    ]
    untracked_runtime = [
        entry["path"]
        for entry in summary.get("untracked", [])
        if isinstance(entry, dict) and entry.get("classification") == "RUNTIME_ARTIFACT"
    ]
    ignored_runtime = [
        entry["path"]
        for entry in summary.get("ignored", [])
        if isinstance(entry, dict) and entry.get("classification") == "RUNTIME_ARTIFACT"
    ]

    repo_hygiene.restore_tracked([str(path) for path in tracked_runtime if path])
    cleanup_report = repo_hygiene.remove_runtime_paths(
        [str(path) for path in untracked_runtime + ignored_runtime if path],
        repo_hygiene.safe_delete_roots(),
        artifacts_dir=artifacts_dir,
    )
    locked_files = [
        str(path) for path in cleanup_report.get("skipped_locked", []) if path
    ]

    final_report = build_report(root)
    status = final_report.status
    reason = final_report.reason
    suggested_actions = list(final_report.suggested_actions)
    if locked_files:
        status = "DEGRADED"
        reason = "locked_files"
        suggested_actions = [
            "close locked files and rerun python -m tools.git_health fix"
        ]
    elif cleanup_report.get("status") == "FAIL":
        status = "FAIL"
        reason = "cleanup_failed"
        suggested_actions = ["review artifacts for cleanup failures"]

    return HealthReport(
        status=status,
        reason=reason,
        repo_root=final_report.repo_root,
        dirty_source_files=final_report.dirty_source_files,
        runtime_untracked=final_report.runtime_untracked,
        unsafe_untracked=final_report.unsafe_untracked,
        locked_files=locked_files,
        git_clean=_git_status_clean(root),
        suggested_actions=suggested_actions,
    )


def _emit_markers(report: HealthReport) -> None:
    blocking = ",".join(report.dirty_source_files) if report.dirty_source_files else "none"
    runtime = ",".join(report.runtime_untracked) if report.runtime_untracked else "none"
    unsafe = ",".join(report.unsafe_untracked) if report.unsafe_untracked else "none"
    locked = ",".join(report.locked_files) if report.locked_files else "none"
    next_steps = ",".join(report.suggested_actions) if report.suggested_actions else "none"
    print("GIT_HEALTH_START")
    print(
        "GIT_HEALTH_SUMMARY|"
        f"status={report.status}|reason={report.reason}|"
        f"blocking={blocking}|runtime={runtime}|unsafe_untracked={unsafe}|"
        f"locked={locked}|clean={str(report.git_clean).lower()}|next={next_steps}"
    )
    print("GIT_HEALTH_END")


def _write_artifacts(report: HealthReport, artifacts_dir: Path) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": report.status,
        "reason": report.reason,
        "repo_root": report.repo_root,
        "dirty_source_files": report.dirty_source_files,
        "runtime_untracked": report.runtime_untracked,
        "unsafe_untracked": report.unsafe_untracked,
        "locked_files": report.locked_files,
        "git_clean": report.git_clean,
        "next_steps": report.suggested_actions,
        "ts_utc": _ts_utc(),
    }
    _write_json(artifacts_dir / ARTIFACT_JSON_NAME, payload)
    text_path = artifacts_dir / ARTIFACT_TEXT_NAME
    summary_lines = [
        "GIT_HEALTH_REPORT_START",
        f"status={report.status}",
        f"reason={report.reason}",
        f"dirty_source_files={','.join(report.dirty_source_files) if report.dirty_source_files else 'none'}",
        f"runtime_untracked={','.join(report.runtime_untracked) if report.runtime_untracked else 'none'}",
        f"unsafe_untracked={','.join(report.unsafe_untracked) if report.unsafe_untracked else 'none'}",
        f"locked_files={','.join(report.locked_files) if report.locked_files else 'none'}",
        f"next_steps={','.join(report.suggested_actions) if report.suggested_actions else 'none'}",
        "GIT_HEALTH_REPORT_END",
    ]
    text_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Git hygiene health report/fix.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report", help="Report git hygiene state.")
    report_parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts"))

    fix_parser = subparsers.add_parser("fix", help="Apply safe git hygiene fix.")
    fix_parser.add_argument("--mode", choices=["safe"], default="safe")
    fix_parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts"))

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    root = repo_root()

    if args.command == "report":
        report = build_report(root)
        _emit_markers(report)
        _write_artifacts(report, args.artifacts_dir)
        return 0 if report.status == "PASS" else 1

    report = apply_safe_fix(root, args.artifacts_dir)
    _emit_markers(report)
    _write_artifacts(report, args.artifacts_dir)
    return 0 if report.status in {"PASS", "DEGRADED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
