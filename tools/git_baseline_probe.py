from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ref_exists(ref: str) -> bool:
    cmd = ["git", "show-ref", "--verify", "--quiet", ref]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return result.returncode == 0


def detect_baseline() -> str | None:
    candidates = [
        ("origin/main", "refs/remotes/origin/main"),
        ("origin/master", "refs/remotes/origin/master"),
        ("main", "refs/heads/main"),
        ("master", "refs/heads/master"),
    ]
    for name, ref in candidates:
        if _ref_exists(ref):
            return name
    return None


def main() -> int:
    baseline = detect_baseline()
    status = "OK" if baseline else "UNAVAILABLE"
    print(f"BASELINE_PROBE|status={status}|baseline={baseline or 'unavailable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
