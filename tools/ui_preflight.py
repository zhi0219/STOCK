from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.compile_check import run_compile_check
from tools.paths import to_repo_relative
from tools.syntax_guard import DEFAULT_EXCLUDES, run as run_syntax_guard


@dataclass(frozen=True)
class PreflightResult:
    status: str
    reason: str
    repo_root: str | None
    python: str
    dirty_files: list[str]
    suggested_actions: list[str]


SENTINELS = (".git", "pyproject.toml", "tools", "scripts")
CRITICAL_FILES = {"tools/ui_app.py", "scripts/run_ui_windows.ps1"}


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_root_from(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if any((candidate / sentinel).exists() for sentinel in SENTINELS):
            return candidate
    return None


def resolve_repo_root(explicit: Path | None = None) -> Path | None:
    if explicit:
        return _repo_root_from(explicit)
    script_dir = Path(__file__).resolve().parent
    return _repo_root_from(script_dir) or _repo_root_from(Path.cwd())


def _run_git_status(root: Path) -> tuple[list[str], str | None]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return [], "git_unavailable"
    if result.returncode != 0:
        return [], "git_status_failed"
    return [line.strip() for line in result.stdout.splitlines() if line.strip()], None


def _extract_dirty_critical(status_lines: list[str]) -> list[str]:
    dirty: list[str] = []
    for line in status_lines:
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path in CRITICAL_FILES:
            dirty.append(path)
    return dirty


def _check_imports() -> list[str]:
    missing: list[str] = []
    try:
        import tkinter  # noqa: F401
    except Exception:
        missing.append("tkinter")
    try:
        import yaml  # noqa: F401
    except Exception:
        missing.append("yaml")
    return missing


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _finalize_result(
    result: PreflightResult,
    artifacts_dir: Path | None,
    extra: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "status": result.status,
        "reason": result.reason,
        "repo_root": result.repo_root,
        "python": result.python,
        "dirty_files": result.dirty_files,
        "suggested_actions": result.suggested_actions,
        "ts_utc": _ts_utc(),
    }
    if extra:
        payload.update(extra)
    if artifacts_dir:
        _write_json(artifacts_dir / "ui_preflight_result.json", payload)


def run_preflight(repo_root: Path) -> PreflightResult:
    repo_root_rel = to_repo_relative(repo_root)
    status_lines, git_error = _run_git_status(repo_root)
    if git_error:
        return PreflightResult(
            status="FAIL",
            reason=git_error,
            repo_root=repo_root_rel,
            python=sys.executable,
            dirty_files=[],
            suggested_actions=["git status --porcelain"],
        )

    dirty_critical = _extract_dirty_critical(status_lines)
    if dirty_critical:
        return PreflightResult(
            status="FAIL",
            reason="dirty_critical_files",
            repo_root=repo_root_rel,
            python=sys.executable,
            dirty_files=dirty_critical,
            suggested_actions=[f"git checkout -- {path}" for path in dirty_critical],
        )

    missing_modules = _check_imports()
    if missing_modules:
        missing_hint = " ".join(missing_modules)
        return PreflightResult(
            status="FAIL",
            reason=f"missing_modules:{missing_hint}",
            repo_root=repo_root_rel,
            python=sys.executable,
            dirty_files=[],
            suggested_actions=[
                ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            ],
        )

    compile_payload = run_compile_check(
        targets=["tools", "scripts", "tests"], artifacts_dir=repo_root / "artifacts"
    )
    if compile_payload.get("status") != "PASS":
        return PreflightResult(
            status="FAIL",
            reason="compile_failed",
            repo_root=repo_root_rel,
            python=sys.executable,
            dirty_files=[],
            suggested_actions=[
                "python -m tools.compile_check --targets tools scripts tests --artifacts-dir artifacts"
            ],
        )

    syntax_rc = run_syntax_guard(
        repo_root, repo_root / "artifacts", set(DEFAULT_EXCLUDES)
    )
    if syntax_rc != 0:
        return PreflightResult(
            status="FAIL",
            reason="syntax_guard_failed",
            repo_root=repo_root_rel,
            python=sys.executable,
            dirty_files=[],
            suggested_actions=["python -m tools.syntax_guard --artifacts-dir artifacts"],
        )

    return PreflightResult(
        status="PASS",
        reason="ok",
        repo_root=repo_root_rel,
        python=sys.executable,
        dirty_files=[],
        suggested_actions=[],
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UI preflight checks.")
    parser.add_argument("--repo-root", type=Path, help="Explicit repo root to use.")
    parser.add_argument("--ci", action="store_true", help="Enable CI artifact output.")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory for CI output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    artifacts_dir = args.artifacts_dir if args.ci else None

    repo_root = resolve_repo_root(args.repo_root)
    if repo_root is None:
        result = PreflightResult(
            status="FAIL",
            reason="repo_root_not_found",
            repo_root=None,
            python=sys.executable,
            dirty_files=[],
            suggested_actions=["Run from the repo root."],
        )
        _finalize_result(result, artifacts_dir)
        print("UI_PREFLIGHT_FAIL|reason=repo_root_not_found")
        return 2

    result = run_preflight(repo_root)
    _finalize_result(result, artifacts_dir)

    print("UI_PREFLIGHT_START")
    print(
        "UI_PREFLIGHT_SUMMARY|"
        f"status={result.status}|reason={result.reason}|python={result.python}"
    )
    print("UI_PREFLIGHT_END")

    if result.status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
