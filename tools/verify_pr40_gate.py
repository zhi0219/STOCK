from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tools import git_health
from tools.ui_preflight import run_ui_preflight

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts"
ESCAPED_FSTRING_PATTERNS = ("f\\\"", "rf\\\"", "fr\\\"")
DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iter_python_files(root: Path, excludes: set[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if any(part in excludes for part in path.parts):
            continue
        yield path


def _scan_escaped_fstrings(root: Path) -> list[str]:
    hits: list[str] = []
    for path in _iter_python_files(root, DEFAULT_EXCLUDES):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if any(pattern in line for pattern in ESCAPED_FSTRING_PATTERNS):
                hits.append(f"{path.relative_to(root)}:{idx}")
                break
    return hits


def _contains_required_gitignore_entries() -> list[str]:
    required = ["Logs/policy_registry.json", "Logs/runtime/"]
    gitignore_path = ROOT / ".gitignore"
    if not gitignore_path.exists():
        return required
    content = gitignore_path.read_text(encoding="utf-8", errors="replace")
    return [entry for entry in required if entry not in content]


def _legacy_registry_tracked() -> bool:
    result = subprocess.run(
        ["git", "ls-files", "Logs/policy_registry.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _set_execution_policy_present() -> bool:
    script_path = ROOT / "scripts" / "run_ui_windows.ps1"
    if not script_path.exists():
        return False
    text = script_path.read_text(encoding="utf-8", errors="replace")
    return "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force" in text


def _ensure_job_summary() -> None:
    summary_path = ARTIFACTS_DIR / "proof_summary.json"
    job_summary_path = ARTIFACTS_DIR / "ci_job_summary.md"
    if not summary_path.exists():
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps({"status": "UNKNOWN", "ts_utc": _iso_now()}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if not job_summary_path.exists():
        job_summary_path.write_text("# CI Job Summary\n\n", encoding="utf-8")


def _excerpt(text: str, limit: int = 240) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    ui_preflight = run_ui_preflight(artifacts_dir=ARTIFACTS_DIR)
    ui_reason = _excerpt(str(ui_preflight.get("reason_detail", "")))
    if ui_preflight.get("status") != "PASS":
        errors.append("ui_preflight_failed")

    escaped_hits = _scan_escaped_fstrings(ROOT)
    if escaped_hits:
        errors.append(f"escaped_fstrings:{len(escaped_hits)}")

    git_health.build_report()

    if _legacy_registry_tracked():
        errors.append("legacy_policy_registry_tracked")

    missing_gitignore = _contains_required_gitignore_entries()
    if missing_gitignore:
        errors.append(f"gitignore_missing:{','.join(missing_gitignore)}")

    if not _set_execution_policy_present():
        errors.append("run_ui_windows_missing_execution_policy")

    _ensure_job_summary()

    if errors:
        print("verify_pr40_gate FAIL")
        for err in errors:
            print(f" - {err}")
        if ui_reason:
            print(f"verify_pr40_gate UI_PREFLIGHT_REASON|{ui_reason}")
        return 1

    print("verify_pr40_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
