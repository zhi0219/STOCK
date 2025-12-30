from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tools.git_health import fix_safe
from tools.paths import repo_root
from tools.ui_preflight import run_ui_preflight


def _log_line(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")
    print(message)


def _run_git(cmd: list[str], root: Path) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *cmd],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(block for block in [result.stdout, result.stderr] if block)
    return result.returncode, output


def _launch_line(root: Path) -> str:
    return f"UI_LAUNCH_CMD|root={root.resolve()}|python={sys.executable}|mode=module"


def main() -> int:
    root = repo_root()
    log_path = root / "Logs" / "runtime" / "launch_ui_windows_latest.log"
    os.chdir(root)
    _log_line(log_path, "UI_PREFLIGHT_START")

    fix_result = fix_safe()
    if fix_result.get("status") != "PASS":
        _log_line(log_path, "UI_LAUNCH_ABORT|reason=git_health_failed")
        return 1

    fetch_code, fetch_output = _run_git(["fetch", "origin", "main"], root)
    if fetch_code != 0:
        _log_line(log_path, f"UI_GIT_FETCH_FAIL|detail={fetch_output.strip()}")
        _log_line(log_path, "UI_LAUNCH_ABORT|reason=git_fetch_failed")
        return 1

    pull_code, pull_output = _run_git(["pull", "--ff-only", "origin", "main"], root)
    if pull_code != 0:
        _log_line(log_path, f"UI_GIT_PULL_FAIL|detail={pull_output.strip()}")
        _log_line(log_path, "UI_LAUNCH_ABORT|reason=git_pull_failed")
        return 1

    _log_line(log_path, "UI_COMPILEALL_START")
    compile_payload = run_ui_preflight(artifacts_dir=root / "artifacts")
    if compile_payload.get("status") != "PASS":
        _log_line(log_path, "UI_COMPILEALL_FAIL|log=artifacts/compile_check.log")
        _log_line(log_path, "UI_LAUNCH_ABORT|reason=compile_failed")
        return 1
    _log_line(log_path, "UI_COMPILEALL_PASS")
    _log_line(log_path, _launch_line(root))
    return subprocess.call([sys.executable, "-m", "tools.ui_app"])


if __name__ == "__main__":
    raise SystemExit(main())
