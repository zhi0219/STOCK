from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tools.ui_preflight import resolve_repo_root, run_preflight


def _launch_line(root: Path) -> str:
    return f"UI_LAUNCH_CMD|root={root.resolve()}|python={sys.executable}|mode=module"


def main() -> int:
    root = resolve_repo_root()
    if root is None:
        print("UI_LAUNCH_FAILED|reason=repo_root_not_found")
        return 2
    os.chdir(root)
    preflight = run_preflight(root)
    if preflight.status != "PASS":
        print(f"UI_PREFLIGHT_FAIL|reason={preflight.reason}")
        for action in preflight.suggested_actions:
            print(f"UI_PREFLIGHT_NEXT|{action}")
        return 1
    print(_launch_line(root))
    return subprocess.call([sys.executable, "-m", "tools.ui_app"])


if __name__ == "__main__":
    raise SystemExit(main())
