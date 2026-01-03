import sys
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools import repo_hygiene
from tools.paths import repo_root, to_repo_relative


ARTIFACT_NAME = "git_health_result.json"


@dataclass(frozen=True)
class HealthReport:
    status: str
    reason: str
    repo_root: str
    blocking_paths: list[str]
    runtime_paths: list[str]
    git_clean: bool
    suggested_actions: list[str]


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _collect_paths(summary: dict[str, Any], classification: str) -> list[str]:
    paths: list[str] = []
    for bucket in ("tracked_modified", "untracked", "ignored"):
        entries = summary.get(bucket, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("classification") == classification:
                path = entry.get("path")
                if path:
                    paths.append(str(path))
    return paths


def _collect_blocking(summary: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for bucket in ("tracked_modified", "untracked"):
        entries = summary.get(bucket, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            classification = entry.get("classification")
            if classification != "RUNTIME_ARTIFACT":
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
            blocking_paths=[],
            runtime_paths=[],
            git_clean=False,
            suggested_actions=["git status --porcelain"],
        )

    summary = repo_hygiene.scan_repo()
    blocking = _collect_blocking(summary)
    runtime_paths = _collect_paths(summary, "RUNTIME_ARTIFACT")
    git_clean = _git_status_clean(root)

    if blocking:
        return HealthReport(
            status="FAIL",
            reason="dirty_source_files",
            repo_root=root_rel,
            blocking_paths=blocking,
            runtime_paths=runtime_paths,
            git_clean=git_clean,
            suggested_actions=["git status --porcelain"],
        )

    return HealthReport(
        status="PASS",
        reason="ok",
        repo_root=root_rel,
        blocking_paths=[],
        runtime_paths=runtime_paths,
        git_clean=git_clean,
        suggested_actions=[],
    )


def apply_safe_fix(root: Path) -> HealthReport:
    report = build_report(root)
    if report.status != "PASS":
        return report

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
    repo_hygiene.remove_runtime_paths(
        [str(path) for path in untracked_runtime + ignored_runtime if path],
        repo_hygiene.safe_delete_roots(),
    )

    final_report = build_report(root)
    return HealthReport(
        status=final_report.status,
        reason=final_report.reason,
        repo_root=final_report.repo_root,
        blocking_paths=final_report.blocking_paths,
        runtime_paths=final_report.runtime_paths,
        git_clean=_git_status_clean(root),
        suggested_actions=final_report.suggested_actions,
    )


def _emit_markers(report: HealthReport) -> None:
    blocking = ",".join(report.blocking_paths) if report.blocking_paths else "none"
    runtime = ",".join(report.runtime_paths) if report.runtime_paths else "none"
    print("GIT_HEALTH_START")
    print(
        "GIT_HEALTH_SUMMARY|"
        f"status={report.status}|reason={report.reason}|"
        f"blocking={blocking}|runtime={runtime}|clean={str(report.git_clean).lower()}"
    )
    print("GIT_HEALTH_END")


def _write_artifacts(report: HealthReport, artifacts_dir: Path) -> None:
    payload = {
        "status": report.status,
        "reason": report.reason,
        "repo_root": report.repo_root,
        "blocking_paths": report.blocking_paths,
        "runtime_paths": report.runtime_paths,
        "git_clean": report.git_clean,
        "suggested_actions": report.suggested_actions,
        "ts_utc": _ts_utc(),
    }
    _write_json(artifacts_dir / ARTIFACT_NAME, payload)


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

    report = apply_safe_fix(root)
    _emit_markers(report)
    _write_artifacts(report, args.artifacts_dir)
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
