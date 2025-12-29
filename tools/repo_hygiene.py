from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from tools.paths import repo_root, runtime_dir

SCHEMA_VERSION = 1
CONFIRM_TOKEN = "DELETE-RUNTIME"

RUNTIME_PREFIXES = [
    "Logs/",
    "Logs/runtime/",
    "Logs/train_runs/",
    "Logs/train_service/",
    "Logs/tournament_runs/",
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
}

SAFE_DELETE_ROOTS = [
    repo_root() / "Logs",
    repo_root() / "Reports",
    repo_root() / "evidence_packs",
    repo_root() / "qa_packets",
    repo_root() / "qa_answers",
    repo_root() / "artifacts",
]

AGGRESSIVE_DELETE_ROOTS = [
    runtime_dir(),
    repo_root() / "artifacts",
]


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


def _remove_runtime_paths(paths: list[str], roots: list[Path]) -> None:
    root = repo_root()
    for rel_path in paths:
        candidate = root / rel_path
        if not _is_under_root(candidate, roots):
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)
        elif candidate.exists():
            candidate.unlink()


def restore_tracked(paths: list[str]) -> None:
    _restore_tracked(paths)


def remove_runtime_paths(paths: list[str], roots: list[Path]) -> None:
    _remove_runtime_paths(paths, roots)


def safe_delete_roots() -> list[Path]:
    return list(SAFE_DELETE_ROOTS)


def fix_repo(mode: str, aggressive: bool, confirm_token: str | None) -> Dict[str, object]:
    if mode == "aggressive":
        if not aggressive or confirm_token != CONFIRM_TOKEN:
            raise ValueError(
                f"Aggressive mode requires --i-know-what-im-doing and --confirm {CONFIRM_TOKEN}"
            )
        for root in AGGRESSIVE_DELETE_ROOTS:
            shutil.rmtree(root, ignore_errors=True)
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
    _remove_runtime_paths(runtime_untracked + runtime_ignored, SAFE_DELETE_ROOTS)
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
