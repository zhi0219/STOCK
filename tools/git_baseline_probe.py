from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _ref_exists(ref: str) -> bool:
    result = _run_git(["show-ref", "--verify", "--quiet", ref])
    return result.returncode == 0


def detect_baseline() -> Optional[str]:
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


def _origin_present() -> bool:
    result = _run_git(["remote"])
    if result.returncode != 0:
        return False
    remotes = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return "origin" in remotes


def _is_shallow_repository() -> Optional[bool]:
    result = _run_git(["rev-parse", "--is-shallow-repository"])
    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    if value in {"true", "false"}:
        return value == "true"
    return None


def probe_baseline() -> Dict[str, Optional[str]]:
    result = _run_git(["rev-parse", "--is-inside-work-tree"])
    if result.returncode != 0:
        return {"status": "UNAVAILABLE", "baseline": None, "details": "git_error"}

    shallow = _is_shallow_repository()
    if shallow is None:
        return {"status": "UNAVAILABLE", "baseline": None, "details": "git_error"}
    if shallow:
        return {"status": "UNAVAILABLE", "baseline": None, "details": "shallow_repo"}

    baseline = detect_baseline()
    if baseline:
        return {"status": "AVAILABLE", "baseline": baseline, "details": f"source={baseline}"}

    if not _origin_present():
        return {"status": "UNAVAILABLE", "baseline": None, "details": "no_origin"}
    return {"status": "UNAVAILABLE", "baseline": None, "details": "no_main_ref"}


def main() -> int:
    info = probe_baseline()
    status = info.get("status") or "UNAVAILABLE"
    baseline = info.get("baseline") or "unavailable"
    details = info.get("details") or "unknown"
    print(f"BASELINE_PROBE|status={status}|baseline={baseline}|details={details}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
