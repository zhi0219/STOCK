from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from tools.paths import repo_root, runtime_dir, to_repo_relative

SCHEMA_VERSION = 1
CONFIRM_TOKEN = "DELETE-RUNTIME"

RUNTIME_PREFIXES = [
    "Logs/",
    "Logs/runtime/",
    "Logs/train_runs/",
    "Logs/train_service/",
    "Logs/tournament_runs/",
    "Logs/event_archives/",
    "Logs/_event_archives/",
    "logs/",
    "logs/runtime/",
    "logs/train_runs/",
    "logs/train_service/",
    "logs/tournament_runs/",
    "Reports/",
    "reports/",
    "evidence_packs/",
    "qa_packets/",
    "qa_answers/",
    "artifacts/",
    "__pycache__/",
]

RUNTIME_REGISTRY_PATHS = {
    "Logs/runtime/policy_registry.json",
    "Logs/policy_registry.json",
    "logs/runtime/policy_registry.json",
    "logs/policy_registry.json",
}

SEED_PATHS = {
    "Data/policy_registry.seed.json",
    "Data/friction_policy.json",
    "Data/retention_policy.json",
    "Data/overtrading_budget.json",
}

SAFE_DELETE_ROOT_NAMES = [
    "Logs",
    "Reports",
    "evidence_packs",
    "qa_packets",
    "qa_answers",
    "artifacts",
]

AGGRESSIVE_DELETE_ROOT_NAMES = [
    "Logs/runtime",
    "artifacts",
]

CLEANUP_SCHEMA_VERSION = 1
CLEANUP_REPORT_NAME = "repo_hygiene_cleanup.json"
CLEANUP_TEXT_NAME = "repo_hygiene_cleanup.txt"
MAX_SKIPPED_DETAILS = 50


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _parse_status_path(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return _normalize_path(path)


def _is_latest_pointer(path: str) -> bool:
    name = Path(path).name.lower()
    return "_latest" in name or name.endswith("latest.json")


def _is_runtime_path(path: str) -> bool:
    normalized = _normalize_path(path)
    if normalized in RUNTIME_REGISTRY_PATHS:
        return True
    if _is_latest_pointer(normalized):
        return True
    return any(normalized.startswith(prefix) for prefix in RUNTIME_PREFIXES)


def _is_seed_path(path: str) -> bool:
    return _normalize_path(path) in SEED_PATHS


def _classify(path: str, is_tracked: bool) -> str:
    if _is_runtime_path(path):
        return "RUNTIME_ARTIFACT"
    if is_tracked and _is_seed_path(path):
        return "SEED_CHANGE"
    if is_tracked:
        return "CODE_CHANGE"
    return "UNKNOWN"


def classify_for_doctor(path: str, is_tracked: bool) -> str:
    if _is_runtime_path(path):
        return "SAFE_RUNTIME_ARTIFACT"
    if is_tracked and _is_seed_path(path):
        return "SAFE_SEED_CHANGE"
    return "UNKNOWN"


def normalize_path(path: str) -> str:
    return _normalize_path(path)


def is_runtime_path(path: str) -> bool:
    return _is_runtime_path(path)


def git_status_porcelain(include_ignored: bool = True) -> tuple[list[str], str | None]:
    cmd = ["git", "status", "--porcelain", "--untracked-files=all"]
    if include_ignored:
        cmd.append("--ignored=matching")
    result = subprocess.run(
        cmd,
        cwd=repo_root(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return [], f"git status failed ({result.returncode})"
    return [line.rstrip("\n") for line in result.stdout.splitlines() if line.strip()], None


def _collect_git_status(include_ignored: bool = True) -> list[str]:
    lines, _error = git_status_porcelain(include_ignored=include_ignored)
    return lines


def _build_entry(path: str, classification: str) -> Dict[str, str]:
    return {"path": path, "classification": classification}


def scan_repo() -> Dict[str, object]:
    status_lines = _collect_git_status(include_ignored=True)
    tracked_modified: List[Dict[str, str]] = []
    untracked: List[Dict[str, str]] = []
    ignored: List[Dict[str, str]] = []

    for line in status_lines:
        if line.startswith("?? "):
            path = _parse_status_path(line)
            untracked.append(_build_entry(path, _classify(path, False)))
        elif line.startswith("!! "):
            path = _parse_status_path(line)
            ignored.append(_build_entry(path, _classify(path, False)))
        else:
            path = _parse_status_path(line)
            tracked_modified.append(_build_entry(path, _classify(path, True)))

    counts = {
        "tracked_modified": len(tracked_modified),
        "untracked": len(untracked),
        "ignored": len(ignored),
        "runtime_artifacts": sum(
            1 for entry in tracked_modified + untracked + ignored if entry["classification"] == "RUNTIME_ARTIFACT"
        ),
        "seed_changes": sum(
            1 for entry in tracked_modified + untracked + ignored if entry["classification"] == "SEED_CHANGE"
        ),
        "code_changes": sum(
            1 for entry in tracked_modified + untracked + ignored if entry["classification"] == "CODE_CHANGE"
        ),
        "unknown": sum(
            1 for entry in tracked_modified + untracked + ignored if entry["classification"] == "UNKNOWN"
        ),
    }

    status = "PASS" if not tracked_modified and not untracked else "FAIL"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "tracked_modified": tracked_modified,
        "untracked": untracked,
        "ignored": ignored,
        "counts": counts,
        "runtime_prefixes": list(RUNTIME_PREFIXES),
    }


def _summary_line(summary: Dict[str, object]) -> str:
    counts = summary.get("counts", {}) if isinstance(summary.get("counts"), dict) else {}
    return "|".join(
        [
            "REPO_HYGIENE_SUMMARY",
            f"status={summary.get('status', 'UNKNOWN')}",
            f"tracked_modified={counts.get('tracked_modified', 0)}",
            f"untracked={counts.get('untracked', 0)}",
            f"ignored={counts.get('ignored', 0)}",
            f"runtime_artifacts={counts.get('runtime_artifacts', 0)}",
            f"seed_changes={counts.get('seed_changes', 0)}",
            f"code_changes={counts.get('code_changes', 0)}",
            f"unknown={counts.get('unknown', 0)}",
        ]
    )


def _emit_summary(summary: Dict[str, object]) -> None:
    print("REPO_HYGIENE_START")
    line = _summary_line(summary)
    print(line)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("REPO_HYGIENE_END")
    print(line)


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_locked_error(exc: Exception) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        return getattr(exc, "winerror", None) == 32
    return False


def _record_skip(skipped: list[dict[str, str]], path: Path, exc: Exception) -> None:
    skipped.append(
        {
            "path": _normalize_path(to_repo_relative(path)),
            "error": repr(exc),
            "locked": str(_is_locked_error(exc)).lower(),
        }
    )


def _safe_unlink(path: Path, skipped: list[dict[str, str]]) -> bool:
    try:
        path.unlink()
        return True
    except Exception as exc:
        _record_skip(skipped, path, exc)
        return False


def _safe_rmdir(path: Path, skipped: list[dict[str, str]]) -> bool:
    try:
        path.rmdir()
        return True
    except Exception as exc:
        _record_skip(skipped, path, exc)
        return False


def _remove_dir(path: Path, skipped: list[dict[str, str]], removed: list[str]) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_dir():
            if _safe_rmdir(child, skipped):
                removed.append(_normalize_path(to_repo_relative(child)))
        else:
            if _safe_unlink(child, skipped):
                removed.append(_normalize_path(to_repo_relative(child)))
    if _safe_rmdir(path, skipped):
        removed.append(_normalize_path(to_repo_relative(path)))


def _write_cleanup_artifacts(report: dict[str, object], artifacts_dir: Path) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifacts_dir / CLEANUP_REPORT_NAME
    text_path = artifacts_dir / CLEANUP_TEXT_NAME
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    summary = [
        "REPO_HYGIENE_CLEANUP_START",
        "|".join(
            [
                "REPO_HYGIENE_CLEANUP_SUMMARY",
                f"status={report.get('status', 'UNKNOWN')}",
                f"removed={report.get('removed_count', 0)}",
                f"skipped={report.get('skipped_count', 0)}",
                f"report={to_repo_relative(json_path)}",
            ]
        ),
    ]
    if report.get("status") == "FAIL":
        summary.append(
            "REPO_HYGIENE_CLEANUP_NEXT="
            "close locked files and rerun python -m tools.git_hygiene_fix"
        )
    summary.append("REPO_HYGIENE_CLEANUP_END")
    text_path.write_text("\n".join(summary) + "\n", encoding="utf-8")


def _is_under_root(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        resolved = path.absolute()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        else:
            return True
    return False


def _restore_tracked(paths: list[str]) -> None:
    if not paths:
        return
    subprocess.run(["git", "restore", "--"] + paths, cwd=repo_root(), check=False)


def _remove_runtime_paths(
    paths: list[str],
    roots: list[Path],
    artifacts_dir: Path,
) -> dict[str, object]:
    root = repo_root()
    removed: list[str] = []
    skipped: list[dict[str, str]] = []
    requested: list[str] = []
    for rel_path in paths:
        candidate = root / rel_path
        if not _is_under_root(candidate, roots):
            continue
        requested.append(_normalize_path(to_repo_relative(candidate)))
        if candidate.is_dir():
            _remove_dir(candidate, skipped, removed)
        elif candidate.exists():
            if _safe_unlink(candidate, skipped):
                removed.append(_normalize_path(to_repo_relative(candidate)))

    locked = [
        entry["path"] for entry in skipped if str(entry.get("locked", "false")).lower() == "true"
    ]
    limited_skips = skipped[:MAX_SKIPPED_DETAILS]
    report = {
        "schema_version": CLEANUP_SCHEMA_VERSION,
        "status": "PASS" if not skipped else "FAIL",
        "ts_utc": _iso_utc(),
        "requested_paths": requested,
        "removed_count": len(removed),
        "skipped_count": len(skipped),
        "skipped": limited_skips,
        "skipped_truncated": len(skipped) > len(limited_skips),
        "skipped_locked": locked,
        "artifacts": {
            "report_json": to_repo_relative(artifacts_dir / CLEANUP_REPORT_NAME),
            "report_text": to_repo_relative(artifacts_dir / CLEANUP_TEXT_NAME),
        },
    }
    _write_cleanup_artifacts(report, artifacts_dir)
    return report


def restore_tracked(paths: list[str]) -> None:
    _restore_tracked(paths)


def remove_runtime_paths(
    paths: list[str],
    roots: list[Path],
    artifacts_dir: Path | None = None,
) -> dict[str, object]:
    return _remove_runtime_paths(paths, roots, artifacts_dir or repo_root() / "artifacts")


def safe_delete_roots() -> list[Path]:
    root = repo_root()
    return [root / name for name in SAFE_DELETE_ROOT_NAMES]


def fix_repo(mode: str, aggressive: bool, confirm_token: str | None) -> Dict[str, object]:
    if mode == "aggressive":
        if not aggressive or confirm_token != CONFIRM_TOKEN:
            raise ValueError(
                f"Aggressive mode requires --i-know-what-im-doing and --confirm {CONFIRM_TOKEN}"
            )
        root = repo_root()
        for name in AGGRESSIVE_DELETE_ROOT_NAMES:
            shutil.rmtree(root / Path(name), ignore_errors=True)
        return scan_repo()

    summary = scan_repo()
    tracked_runtime = [
        entry["path"]
        for entry in summary.get("tracked_modified", [])
        if entry.get("classification") == "RUNTIME_ARTIFACT"
    ]
    _restore_tracked(tracked_runtime)

    runtime_untracked = [
        entry["path"]
        for entry in summary.get("untracked", [])
        if entry.get("classification") == "RUNTIME_ARTIFACT"
    ]
    runtime_ignored = [
        entry["path"]
        for entry in summary.get("ignored", [])
        if entry.get("classification") == "RUNTIME_ARTIFACT"
    ]
    _remove_runtime_paths(runtime_untracked + runtime_ignored, safe_delete_roots())
    return scan_repo()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repo hygiene scan/fix")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan repo hygiene state")
    scan_parser.set_defaults(mode="scan")

    fix_parser = subparsers.add_parser("fix", help="Fix runtime artifacts safely")
    fix_parser.add_argument("--mode", choices=["safe", "aggressive"], default="safe")
    fix_parser.add_argument("--i-know-what-im-doing", action="store_true")
    fix_parser.add_argument("--confirm", help=f"Confirmation token for aggressive mode: {CONFIRM_TOKEN}")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])

    if args.command == "scan":
        summary = scan_repo()
        _emit_summary(summary)
        return 0 if summary.get("status") == "PASS" else 1

    try:
        summary = fix_repo(args.mode, args.i_know_what_im_doing, args.confirm)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    _emit_summary(summary)
    return 0 if summary.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
