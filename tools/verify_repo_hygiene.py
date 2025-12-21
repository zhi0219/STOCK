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

SUSPECT_PREFIXES = [
    "Logs/events",
    "logs/events",
    "Logs/status.json",
    "logs/status.json",
    "Logs/policy_registry.json",
    "logs/policy_registry.json",
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


def _find_untracked_matches(status_lines: list[str]) -> list[str]:
    matches: list[str] = []
    for line in status_lines:
        if not line.startswith("?? "):
            continue
        path = line[3:]
        for prefix in SUSPECT_PREFIXES:
            if path.startswith(prefix):
                matches.append(path)
                break
    return matches


def main() -> int:
    content = _read_gitignore()
    missing = _missing_rules(content)
    status_lines = _collect_git_status()
    untracked_matches = _find_untracked_matches(status_lines)

    status = "PASS" if not missing and not untracked_matches else "FAIL"
    if missing:
        print(f"Missing .gitignore rules: {', '.join(missing)}")
    if untracked_matches:
        print(
            "Untracked runtime artifacts detected: "
            + ", ".join(sorted(set(untracked_matches)))
        )

    summary = "|".join(
        [
            "REPO_HYGIENE_SUMMARY",
            f"status={status}",
            f"missing_rules={','.join(missing) if missing else 'none'}",
            f"untracked_matches={','.join(untracked_matches) if untracked_matches else 'none'}",
        ]
    )
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
