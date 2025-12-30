from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tools.compile_check import run_compile_check


def _find_repo_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return None


def _resolve_repo_root() -> Path | None:
    candidates = [Path.cwd(), Path(__file__).resolve()]
    for start in candidates:
        root = _find_repo_root(start)
        if root:
            return root
    return None


def _launch_line(root: Path) -> str:
    return f"UI_LAUNCH_CMD|root={root.resolve()}|python={sys.executable}|mode=module"


def main() -> int:
    root = _resolve_repo_root()
    if root is None:
        print("UI_LAUNCH_FAILED|reason=repo_root_not_found")
        return 2
    os.chdir(root)
    print("UI_PREFLIGHT_START")
    print("UI_COMPILEALL_START")
    compile_payload = run_compile_check(targets=["tools"], artifacts_dir=root / "artifacts")
    if compile_payload.get("status") != "PASS":
        log_path = compile_payload.get("log_path", "artifacts/compile_check.log")
        print(f"UI_COMPILEALL_FAIL|log={log_path}")
        print("UI_LAUNCH_ABORT|reason=compile_failed")
        return 1
    print("UI_COMPILEALL_PASS")
    print(_launch_line(root))
    return subprocess.call([sys.executable, "-m", "tools.ui_app"])


if __name__ == "__main__":
    raise SystemExit(main())
