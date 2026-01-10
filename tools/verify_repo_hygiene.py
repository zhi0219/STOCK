from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fs_atomic import atomic_write_text

REPO_HYGIENE_ARTIFACTS = [
    "artifacts/repo_hygiene_untracked.json",
]

REQUIRED_RULES = [
    "Logs/events*.jsonl",
    "logs/events*.jsonl",
    "Logs/status.json",
    "logs/status.json",
    "Logs/policy_registry.json",
    "Logs/data_runtime/",
    "Logs/runtime/",
    "logs/policy_registry.json",
    "logs/data_runtime/",
    "logs/runtime/",
    "Logs/train_daemon_state.json",
    "logs/train_daemon_state.json",
    "Logs/train_runs/",
    "logs/train_runs/",
    "Logs/progress_index.json",
    "logs/progress_index.json",
    "Logs/baseline_guide.txt",
    "logs/baseline_guide.txt",
    "Logs/*latest*.json",
    "logs/*latest*.json",
    "Logs/train_service/",
    "logs/train_service/",
    "Logs/tournament_runs/",
    "logs/tournament_runs/",
    "Logs/event_archives/",
    "Logs/_event_archives/",
    "evidence_packs/",
    "qa_packets/",
    "qa_answers/",
    "Reports/",
    "artifacts/",
]

RUNTIME_ROOTS = [
    "Logs/",
    "Logs/data_runtime/",
    "Logs/runtime/",
    "Logs/event_archives/",
    "Logs/_event_archives/",
    "logs/",
    "logs/data_runtime/",
    "logs/runtime/",
    "Reports/",
    "reports/",
    "evidence_packs/",
    "qa_packets/",
    "qa_answers/",
]

HIGHLIGHT_PATHS = {
    "Logs/train_daemon_state.json",
    "logs/train_daemon_state.json",
    "Logs/progress_index.json",
    "logs/progress_index.json",
}

SOURCE_LIKE_ROOTS = [
    "scripts/",
    "tools/",
    "tests/",
    "docs/",
    ".github/",
    ".githooks/",
    "fixtures/",
]
RUNTIME_LIKE_ROOTS = [
    "artifacts/",
    "work/",
    "logs/",
    "Logs/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "__pycache__/",
]

LEGACY_ARCHIVE_DIR = ROOT / "Logs" / "_event_archives"
ARCHIVE_PATTERN = "events_*.jsonl"


def _read_gitignore(gitignore_path: Path) -> str:
    if not gitignore_path.exists():
        return ""
    return gitignore_path.read_text(encoding="utf-8")


def _missing_rules(content: str) -> list[str]:
    return [rule for rule in REQUIRED_RULES if rule not in content]


def _collect_git_status(repo_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return []

    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _collect_untracked(status_lines: list[str]) -> list[str]:
    return [line[3:] for line in status_lines if line.startswith("?? ")]


def _runtime_untracked(untracked: list[str]) -> list[str]:
    return [p for p in untracked if any(p.startswith(prefix) for prefix in RUNTIME_ROOTS)]


def _unsafe_untracked(untracked: list[str]) -> list[str]:
    return [p for p in untracked if not any(p.startswith(prefix) for prefix in RUNTIME_ROOTS)]


def _highlighted(untracked: list[str]) -> list[str]:
    return [p for p in untracked if p in HIGHLIGHT_PATHS]


def _format_list(items: list[str]) -> str:
    return ",".join(items) if items else "none"

def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def _classify_untracked(untracked: list[str]) -> dict[str, list[str]]:
    source_like: list[str] = []
    runtime_like: list[str] = []
    other: list[str] = []
    for raw in untracked:
        normalized = _normalize_repo_path(raw)
        if any(normalized.startswith(prefix) for prefix in SOURCE_LIKE_ROOTS):
            source_like.append(normalized)
        elif any(normalized.startswith(prefix) for prefix in RUNTIME_LIKE_ROOTS):
            runtime_like.append(normalized)
        else:
            other.append(normalized)
    return {
        "source_like": sorted(source_like),
        "runtime_like": sorted(runtime_like),
        "other": sorted(other),
    }


def _truncate_list(values: list[str], limit: int) -> list[str]:
    if len(values) <= limit:
        return values
    return values[:limit] + ["..."]


def _write_untracked_artifact(
    artifacts_dir: Path,
    total: int,
    source_like: list[str],
    runtime_like: list[str],
    other: list[str],
    hint_paths: list[str],
    sample_limit: int,
) -> None:
    payload = {
        "total": total,
        "counts": {
            "source_like": len(source_like),
            "runtime_like": len(runtime_like),
            "other": len(other),
        },
        "sample": {
            "source_like": _truncate_list(source_like, sample_limit),
            "runtime_like": _truncate_list(runtime_like, sample_limit),
            "other": _truncate_list(other, sample_limit),
        },
        "hint_git_add": hint_paths,
    }
    artifact_path = artifacts_dir / "repo_hygiene_untracked.json"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(artifact_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify repo hygiene for untracked or missing rules.")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--artifacts-dir", default=None)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else ROOT
    artifacts_dir = (
        Path(args.artifacts_dir).resolve()
        if args.artifacts_dir
        else repo_root / "artifacts"
    )
    gitignore_path = repo_root / ".gitignore"
    legacy_archive_dir = repo_root / "Logs" / "_event_archives"

    content = _read_gitignore(gitignore_path)
    missing = _missing_rules(content)
    status_lines = _collect_git_status(repo_root)
    untracked = _collect_untracked(status_lines)
    runtime_untracked = _runtime_untracked(untracked)
    unsafe_untracked = _unsafe_untracked(untracked)
    highlighted = _highlighted(runtime_untracked)
    legacy_archives = (
        sorted(legacy_archive_dir.glob(ARCHIVE_PATTERN))
        if legacy_archive_dir.exists()
        else []
    )

    classified = _classify_untracked(untracked)
    source_like = classified["source_like"]
    runtime_like = classified["runtime_like"]
    other = classified["other"]
    hint_paths = _truncate_list(source_like, 20)

    status = "PASS" if not missing and not runtime_untracked and not unsafe_untracked else "FAIL"

    summary = "|".join(
        [
            "REPO_HYGIENE_SUMMARY",
            f"status={status}",
            f"missing_rules={_format_list(missing)}",
            f"runtime_untracked={_format_list(runtime_untracked)}",
            f"unsafe_untracked={_format_list(unsafe_untracked)}",
            f"highlights={_format_list(highlighted)}",
        ]
    )

    print("REPO_HYGIENE_START")
    print(summary)

    if missing:
        print(f"Missing .gitignore rules: {', '.join(missing)}")

    if runtime_untracked:
        print(
            "Untracked runtime artifacts detected: "
            + ", ".join(sorted(set(runtime_untracked)))
        )

    if highlighted:
        print(
            "Highlight (known runtime patterns): "
            + ", ".join(sorted(set(highlighted)))
        )

    if unsafe_untracked:
        print(
            "Unsafe to delete automatically (manual review required): "
            + ", ".join(sorted(set(unsafe_untracked)))
        )

    if untracked:
        print(
            "REPO_HYGIENE_UNTRACKED"
            f"|total={len(untracked)}"
            f"|source_like={len(source_like)}"
            f"|runtime_like={len(runtime_like)}"
            f"|other={len(other)}"
        )
        if source_like:
            hint_text = " ".join(hint_paths)
            print(f"REPO_HYGIENE_HINT_GIT_ADD|paths={hint_text}")
        _write_untracked_artifact(
            artifacts_dir=artifacts_dir,
            total=len(untracked),
            source_like=source_like,
            runtime_like=runtime_like,
            other=other,
            hint_paths=hint_paths,
            sample_limit=20,
        )

    if legacy_archives:
        print(
            "REPO_HYGIENE_LEGACY_EVENT_ARCHIVES|"
            f"count={len(legacy_archives)}|"
            "path=Logs/_event_archives|"
            "next=python -m tools.migrate_event_archives --logs-dir Logs --archive-dir Logs/event_archives "
            "--artifacts-dir artifacts --mode move"
        )

    print("REPO_HYGIENE_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
