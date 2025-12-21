from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ui_app import run_verify_script


VERIFY_TARGETS = [
    "verify_smoke.py",
    "verify_e2e_qa_loop.py",
    "verify_ui_actions.py",
]


def run() -> int:
    failures = []
    for target in VERIFY_TARGETS:
        result = run_verify_script(target)
        print(result.format_lines())
        if result.returncode != 0:
            failures.append(target)
    if failures:
        print(f"FAIL: {', '.join(failures)}")
        return 1
    print("PASS: all verify scripts exited 0")
    return 0


if __name__ == "__main__":
    sys.exit(run())
