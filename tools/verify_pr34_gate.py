from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from tools.paths import to_repo_relative
from tools.ui_parsers import load_recent_runs_index

ARTIFACTS_DIR = Path("artifacts")
LOGS_DIR = Path("Logs")
RUNS_DIR = LOGS_DIR / "train_runs"
RUNTIME_DIR = LOGS_DIR / "runtime"
RETENTION_REPORT_ARTIFACT = ARTIFACTS_DIR / "retention_report.json"
PRUNE_PLAN_RUNTIME = RUNTIME_DIR / "retention_prune_plan.json"
PRUNE_RESULT_RUNTIME = RUNTIME_DIR / "retention_prune_result.json"
PRUNE_PLAN_ARTIFACT = ARTIFACTS_DIR / "retention_prune_plan.json"
PRUNE_RESULT_ARTIFACT = ARTIFACTS_DIR / "retention_prune_result.json"
RECENT_INDEX_PATH = RUNS_DIR / "recent_runs_index.json"

ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\")


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _contains_absolute_path(text: str) -> bool:
    if not text:
        return False
    if text.startswith("/"):
        return True
    if ABS_PATH_PATTERN.search(text):
        return True
    if re.match(r"^[A-Za-z]:", text):
        return True
    if "\\Users\\" in text:
        return True
    if "/home/runner/" in text or "/workspace/" in text:
        return True
    return False


def _collect_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _collect_strings(key)
            yield from _collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_strings(item)
    elif isinstance(value, str):
        yield value


def _assert_repo_relative(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for text in _collect_strings(payload):
        if _contains_absolute_path(text):
            errors.append(f"absolute_path_detected:{text}")
    return errors


def _run_command(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = "\n".join(block for block in [result.stdout, result.stderr] if block)
    return result.returncode, combined.strip()


def _load_tracked_paths() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return set()
    entries = result.stdout.split("\x00")
    return {entry.strip() for entry in entries if entry.strip()}


def _copy_if_exists(source: Path, target: Path) -> None:
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _collect_latest_pointer_run_ids() -> set[str]:
    latest_dir = RUNS_DIR / "_latest"
    run_ids: set[str] = set()
    if not latest_dir.exists():
        return run_ids
    for path in latest_dir.glob("*.json"):
        payload = _safe_read_json(path)
        run_id = payload.get("run_id")
        if run_id:
            run_ids.add(str(run_id))
    return run_ids


def _run_id_for_dir(run_dir: Path) -> str:
    payload = _safe_read_json(run_dir / "run_complete.json")
    run_id = payload.get("run_id")
    return str(run_id) if run_id else run_dir.name


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    report_cmd = [sys.executable, "-m", "tools.retention_engine", "report", "--output", str(RETENTION_REPORT_ARTIFACT)]
    rc, output = _run_command(report_cmd)
    if rc != 0:
        errors.append(f"retention_report_failed:{output}")

    report_payload = _safe_read_json(RETENTION_REPORT_ARTIFACT)
    if not report_payload:
        errors.append("retention_report_missing_or_invalid")
    else:
        errors.extend(_assert_repo_relative(report_payload))

    prune_cmd = [sys.executable, "-m", "tools.retention_engine", "prune", "--mode", "safe", "--dry-run"]
    rc, output = _run_command(prune_cmd)
    if rc != 0:
        errors.append(f"retention_prune_failed:{output}")

    _copy_if_exists(PRUNE_PLAN_RUNTIME, PRUNE_PLAN_ARTIFACT)
    _copy_if_exists(PRUNE_RESULT_RUNTIME, PRUNE_RESULT_ARTIFACT)

    plan_payload = _safe_read_json(PRUNE_PLAN_ARTIFACT)
    result_payload = _safe_read_json(PRUNE_RESULT_ARTIFACT)
    if not plan_payload:
        errors.append("retention_prune_plan_missing")
    if not result_payload:
        errors.append("retention_prune_result_missing")

    if plan_payload and not plan_payload.get("dry_run", False):
        errors.append("retention_prune_not_dry_run")

    if plan_payload:
        errors.extend(_assert_repo_relative(plan_payload))
    if result_payload:
        errors.extend(_assert_repo_relative(result_payload))

    tracked_paths = _load_tracked_paths()
    delete_candidates = []
    for entry in plan_payload.get("candidates", []) if isinstance(plan_payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        path_rel = str(entry.get("path_rel") or "")
        if not path_rel:
            continue
        delete_candidates.append(path_rel)
        if path_rel in tracked_paths:
            errors.append(f"tracked_path_in_prune_plan:{path_rel}")
        if "/_latest/" in path_rel or path_rel.endswith("_latest.json") or path_rel.endswith("_latest.jsonl"):
            errors.append(f"latest_pointer_in_prune_plan:{path_rel}")

    latest_run_ids = _collect_latest_pointer_run_ids()
    if latest_run_ids and delete_candidates:
        run_dirs = [path for path in RUNS_DIR.iterdir() if path.is_dir() and not path.name.startswith("_")]
        run_id_map = {str(_run_id_for_dir(path)): path for path in run_dirs}
        for run_id in latest_run_ids:
            run_dir = run_id_map.get(run_id)
            if run_dir and to_repo_relative(run_dir) in delete_candidates:
                errors.append(f"latest_pointer_run_pruned:{run_id}")

    recent_cmd = [sys.executable, "-m", "tools.recent_runs_index"]
    rc, output = _run_command(recent_cmd)
    if rc != 0:
        errors.append(f"recent_runs_index_failed:{output}")

    if not RECENT_INDEX_PATH.exists():
        errors.append("recent_runs_index_missing")
    else:
        recent_payload = load_recent_runs_index()
        if recent_payload.get("status") == "missing":
            errors.append("recent_runs_index_unreadable")
        _copy_if_exists(RECENT_INDEX_PATH, ARTIFACTS_DIR / "Logs" / "train_runs" / "recent_runs_index.json")

    if os.environ.get("PR34_FORCE_FAIL") == "1":
        errors.append("PR34_FORCE_FAIL")

    if errors:
        print("verify_pr34_gate FAIL")
        for reason in errors:
            print(f" - {reason}")
        return 1

    print("verify_pr34_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
