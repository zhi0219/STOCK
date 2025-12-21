from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITIGNORE_PATH = ROOT / ".gitignore"

REQUIRED_RULES = [
    "Logs/events*.jsonl",
    "logs/events*.jsonl",
    "Logs/status.json",
    "logs/status.json",
    "Logs/policy_registry.json",
    "logs/policy_registry.json",
    "Logs/train_daemon_state.json",
    "logs/train_daemon_state.json",
    "Logs/train_runs/",
    "logs/train_runs/",
    "Logs/train_service/",
    "logs/train_service/",
    "Logs/tournament_runs/",
    "logs/tournament_runs/",
    "evidence_packs/",
    "qa_packets/",
    "qa_answers/",
    "Reports/",
]

RUNTIME_ROOTS = [
    "Logs/",
    "logs/",
    "Reports/",
    "reports/",
    "evidence_packs/",
    "qa_packets/",
    "qa_answers/",
]

HIGHLIGHT_PATHS = {
    "Logs/train_daemon_state.json",
    "logs/train_daemon_state.json",
}


def _read_gitignore() -> str:
    if not GITIGNORE_PATH.exists():
        return ""
    return GITIGNORE_PATH.read_text(encoding="utf-8")


def _missing_rules(content: str) -> list[str]:
    return [rule for rule in REQUIRED_RULES if rule not in content]


def _collect_git_status() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
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


def main() -> int:
    content = _read_gitignore()
    missing = _missing_rules(content)
    status_lines = _collect_git_status()
    untracked = _collect_untracked(status_lines)
    runtime_untracked = _runtime_untracked(untracked)
    unsafe_untracked = _unsafe_untracked(untracked)
    highlighted = _highlighted(runtime_untracked)

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

    print("REPO_HYGIENE_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
