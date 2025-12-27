from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_VENV = ROOT / ".venv" / "Scripts" / "python.exe"
POSIX_VENV = ROOT / ".venv" / "bin" / "python"


def pick_python(print_marker: bool = True) -> str:
    candidates = [
        ("windows_venv", WINDOWS_VENV),
        ("git_bash_venv", Path("./.venv/Scripts/python.exe")),
        ("posix_venv", POSIX_VENV),
    ]
    chosen = None
    reason = "sys_executable"
    for label, path in candidates:
        if path.exists():
            chosen = path
            reason = label
            break
    if chosen is None:
        chosen = Path(sys.executable)
    using_venv = 1 if ".venv" in chosen.parts else 0
    degraded = 0 if using_venv else 1
    if print_marker:
        print(
            "PY_PICK|path="
            + str(chosen)
            + f"|using_venv={using_venv}|degraded={degraded}|reason={reason}"
        )
    return str(chosen)


if __name__ == "__main__":
    pick_python(print_marker=True)
