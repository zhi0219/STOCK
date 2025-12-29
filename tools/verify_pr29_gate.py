from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from tools import repo_hygiene
from tools.policy_registry import load_registry

ARTIFACTS_DIR = Path("artifacts")
PROOF_SUMMARY_PATH = ARTIFACTS_DIR / "proof_summary.json"
JOB_SUMMARY_PATH = ARTIFACTS_DIR / "ci_job_summary.md"
REPO_HYGIENE_PATH = ARTIFACTS_DIR / "repo_hygiene.json"


def _read_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _update_job_summary(lines: List[str]) -> None:
    existing = ""
    if JOB_SUMMARY_PATH.exists():
        existing = JOB_SUMMARY_PATH.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"
    JOB_SUMMARY_PATH.write_text(existing + "\n".join(lines).strip() + "\n", encoding="utf-8")


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    errors: List[str] = []

    scan = repo_hygiene.scan_repo()
    _write_json(REPO_HYGIENE_PATH, scan)

    if scan.get("status") != "PASS":
        errors.append("repo_hygiene_scan_failed")

    load_registry()

    post_scan = repo_hygiene.scan_repo()
    tracked_modified = post_scan.get("tracked_modified", [])
    untracked = post_scan.get("untracked", [])
    unsafe_untracked = [
        entry for entry in untracked if entry.get("classification") != "RUNTIME_ARTIFACT"
    ]

    if tracked_modified:
        errors.append("runtime_write_modified_tracked_files")
    if unsafe_untracked:
        errors.append("runtime_write_left_unsafe_untracked")

    runtime_write_clean = not tracked_modified and not unsafe_untracked

    proof_summary = _read_json(PROOF_SUMMARY_PATH)
    proof_summary["repo_hygiene"] = {
        "scan_status": scan.get("status"),
        "scan_counts": scan.get("counts", {}),
        "runtime_write_clean": runtime_write_clean,
        "tracked_modified_after_runtime": len(tracked_modified),
        "unsafe_untracked_after_runtime": len(unsafe_untracked),
    }
    _write_json(PROOF_SUMMARY_PATH, proof_summary)

    job_lines = [
        "## Repo hygiene (PR29)",
        f"- scan_status: `{scan.get('status', 'UNKNOWN')}`",
        f"- tracked_modified: `{scan.get('counts', {}).get('tracked_modified', 0)}`",
        f"- untracked: `{scan.get('counts', {}).get('untracked', 0)}`",
        f"- ignored: `{scan.get('counts', {}).get('ignored', 0)}`",
        f"- runtime_write_clean: `{runtime_write_clean}`",
    ]
    _update_job_summary(job_lines)

    if errors:
        print("verify_pr29_gate FAIL")
        for err in errors:
            print(f" - {err}")
        return 1

    print("verify_pr29_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
