from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.paths import repo_root, runtime_dir, to_repo_relative
from tools.repo_hygiene import scan_repo
from tools import repo_hygiene

ROOT = repo_root()
ARTIFACTS_DIR = ROOT / "artifacts"
DEFAULT_OUTPUT = ARTIFACTS_DIR / "doctor_report.json"
LOGS_DIR = ROOT / "Logs"
RUNTIME_DIR = runtime_dir()
TRADE_ACTIVITY_REPORT_PATH = LOGS_DIR / "train_runs" / "_latest" / "trade_activity_report_latest.json"
KILL_SWITCH_PATHS = [ROOT / "Data" / "KILL_SWITCH", LOGS_DIR / "train_service" / "KILL_SWITCH"]
TEMP_FILE_THRESHOLD_SECONDS = 3600
TEMP_FILE_PATTERNS = (".tmp", ".tmp.")
ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\")
MAX_GIT_DIRTY_FILES = 20

ACTION_GEN_DOCTOR_REPORT = "GEN_DOCTOR_REPORT"
ACTION_REPO_HYGIENE_FIX_SAFE = "REPO_HYGIENE_FIX_SAFE"
ACTION_FIX_GIT_RED_SAFE = "FIX_GIT_RED_SAFE"
ACTION_REVIEW_GIT_DIRTY = "REVIEW_GIT_DIRTY"
ACTION_CLEAR_KILL_SWITCH = "CLEAR_KILL_SWITCH"
ACTION_CLEAR_STALE_TEMP = "CLEAR_STALE_TEMP"
ACTION_ENSURE_RUNTIME_DIRS = "ENSURE_RUNTIME_DIRS"
ACTION_DIAG_RUNTIME_WRITE = "DIAG_RUNTIME_WRITE"
ACTION_ABS_PATH_SANITIZE_HINT = "ABS_PATH_SANITIZE_HINT"
ACTION_ENABLE_GIT_HOOKS = "ENABLE_GIT_HOOKS"
ACTION_RUN_RETENTION_REPORT = "RUN_RETENTION_REPORT"
ACTION_PRUNE_OLD_RUNS_SAFE = "PRUNE_OLD_RUNS_SAFE"
ACTION_REBUILD_RECENT_INDEX = "REBUILD_RECENT_INDEX"
ACTION_ENABLE_OVERTRADING_GUARDRAILS = "ENABLE_OVERTRADING_GUARDRAILS_SAFE"

IMPORT_CHECK_MODULES = ("tools.action_center_report", "tools.action_center_apply", "tools.doctor_report")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _now().isoformat()


def _repo_root_hash(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def _python_executable() -> str:
    try:
        return Path(sys.executable).name
    except Exception:
        return "python"


def _sanitize_message(text: str) -> str:
    if not text:
        return text
    root_text = str(ROOT.resolve())
    return text.replace(root_text, "<repo_root>")


def _runtime_write_check(target_dir: Path, retries: int = 3) -> dict[str, Any]:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "doctor_write_probe.json"
    payload = {"ts_utc": _iso_now(), "probe": "runtime_write"}
    last_error: dict[str, Any] | None = None
    for attempt in range(1, retries + 1):
        tmp_path = target_path.with_name(f".{target_path.name}.tmp.{os.getpid()}.{attempt}")
        try:
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp_path, target_path)
            target_path.unlink(missing_ok=True)
            return {"status": "PASS", "attempts": attempt, "path": to_repo_relative(target_path)}
        except OSError as exc:
            last_error = {
                "error_type": type(exc).__name__,
                "error_message": _sanitize_message(str(exc)),
                "winerror": getattr(exc, "winerror", None),
                "attempt": attempt,
                "path": to_repo_relative(target_path),
            }
            time.sleep(0.05 * attempt)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
    return {"status": "FAIL", "error": last_error or {"error_message": "unknown error"}}


def runtime_write_check(target_dir: Path, retries: int = 3) -> dict[str, Any]:
    return _runtime_write_check(target_dir, retries=retries)


def _find_temp_files(paths: Iterable[Path], threshold_seconds: int) -> list[Path]:
    stale: list[Path] = []
    now = time.time()
    for root in paths:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if ".tmp" not in name:
                continue
            if not any(token in name for token in TEMP_FILE_PATTERNS):
                continue
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age >= threshold_seconds:
                stale.append(path)
    return stale


def find_stale_temp_files(paths: Iterable[Path], threshold_seconds: int) -> list[Path]:
    return _find_temp_files(paths, threshold_seconds)


def _scan_absolute_paths(paths: Iterable[Path]) -> list[Path]:
    leaked: list[Path] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if ABS_PATH_PATTERN.search(text):
            leaked.append(path)
    return leaked


def _parse_status_path(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return repo_hygiene.normalize_path(path)


def _collect_git_dirty_files(status_lines: list[str], limit: int) -> list[dict[str, str]]:
    dirty: list[dict[str, str]] = []
    for line in status_lines[:limit]:
        if not line:
            continue
        path = _parse_status_path(line)
        is_tracked = not (line.startswith("?? ") or line.startswith("!! "))
        classification = repo_hygiene.classify_for_doctor(path, is_tracked)
        dirty.append({"path": path, "classification": classification})
    return dirty


def scan_absolute_paths(paths: Iterable[Path]) -> list[Path]:
    return _scan_absolute_paths(paths)


def _load_import_contract_result() -> dict[str, Any] | None:
    path = ARTIFACTS_DIR / "import_contract_result.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_retention_report() -> tuple[dict[str, Any] | None, list[str]]:
    candidates = [
        RUNTIME_DIR / "retention_report.json",
        ARTIFACTS_DIR / "retention_report.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None, [to_repo_relative(path)]
        if isinstance(payload, dict):
            return payload, [to_repo_relative(path)]
    return None, []


def _run_import_sanity_check(modules: Iterable[str]) -> dict[str, Any]:
    failures: list[str] = []
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:
            failures.append(f"{module}:{type(exc).__name__}")
    status = "PASS" if not failures else "FAIL"
    return {"status": status, "module_failures": failures}


def build_report() -> dict[str, Any]:
    now = _now()
    issues: list[dict[str, Any]] = []

    venv_detected = bool(os.environ.get("VIRTUAL_ENV") or (ROOT / ".venv").exists())
    kill_switch_present = any(path.exists() for path in KILL_SWITCH_PATHS)

    if kill_switch_present:
        issues.append(
            {
                "id": "KILL_SWITCH_PRESENT",
                "severity": "HIGH",
                "summary": "Kill switch file present; training/services halted.",
                "evidence_paths_rel": [to_repo_relative(path) for path in KILL_SWITCH_PATHS if path.exists()],
                "suggested_actions": [ACTION_CLEAR_KILL_SWITCH],
            }
        )

    runtime_write_health = _runtime_write_check(RUNTIME_DIR)
    if runtime_write_health.get("status") != "PASS":
        issues.append(
            {
                "id": "RUNTIME_WRITE_FAILED",
                "severity": "HIGH",
                "summary": "Runtime atomic write test failed.",
                "evidence_paths_rel": [to_repo_relative(RUNTIME_DIR)],
                "suggested_actions": [ACTION_ENSURE_RUNTIME_DIRS, ACTION_DIAG_RUNTIME_WRITE],
            }
        )

    stale_temp_files = _find_temp_files([LOGS_DIR, RUNTIME_DIR], TEMP_FILE_THRESHOLD_SECONDS)
    if stale_temp_files:
        issues.append(
            {
                "id": "STALE_TEMP_FILES",
                "severity": "WARN",
                "summary": f"Detected {len(stale_temp_files)} stale temp file(s) older than threshold.",
                "evidence_paths_rel": [to_repo_relative(path) for path in stale_temp_files],
                "suggested_actions": [ACTION_CLEAR_STALE_TEMP],
            }
        )

    hygiene = scan_repo()
    counts = hygiene.get("counts", {}) if isinstance(hygiene.get("counts"), dict) else {}
    if hygiene.get("status") != "PASS":
        suggested_actions: list[str] = []
        if counts.get("runtime_artifacts", 0) > 0:
            suggested_actions.append(ACTION_FIX_GIT_RED_SAFE)
        if counts.get("unknown", 0) > 0:
            suggested_actions.append(ACTION_REVIEW_GIT_DIRTY)
        if not suggested_actions:
            suggested_actions.append(ACTION_REPO_HYGIENE_FIX_SAFE)
        issues.append(
            {
                "id": "REPO_HYGIENE_ISSUE",
                "severity": "WARN",
                "summary": "Repository hygiene scan detected tracked or untracked files.",
                "evidence_paths_rel": ["artifacts/doctor_report.json"],
                "suggested_actions": suggested_actions,
            }
        )

    artifact_candidates = [
        ARTIFACTS_DIR / "action_center_report.json",
        ARTIFACTS_DIR / "action_center_apply_result.json",
        ARTIFACTS_DIR / "proof_summary.json",
        ARTIFACTS_DIR / "gates.log",
        ARTIFACTS_DIR / "ci_job_summary.md",
        ARTIFACTS_DIR / "import_contract_result.json",
    ]
    leaked_paths = _scan_absolute_paths(artifact_candidates)
    if leaked_paths:
        issues.append(
            {
                "id": "ABSOLUTE_PATH_LEAK",
                "severity": "WARN",
                "summary": "Absolute path patterns detected in artifacts (Windows-style).",
                "evidence_paths_rel": [to_repo_relative(path) for path in leaked_paths],
                "suggested_actions": [ACTION_ABS_PATH_SANITIZE_HINT],
            }
        )

    import_contract = _load_import_contract_result()
    import_status = None
    if import_contract:
        import_status = str(import_contract.get("status", "UNKNOWN"))
    else:
        import_contract = _run_import_sanity_check(IMPORT_CHECK_MODULES)
        import_status = import_contract.get("status")
    if import_status and import_status != "PASS":
        issues.append(
            {
                "id": "IMPORT_CONTRACT_FAIL",
                "severity": "WARN",
                "summary": "Import/entrypoint sanity check failed.",
                "evidence_paths_rel": ["artifacts/import_contract_result.json"],
                "suggested_actions": [],
            }
        )

    retention_payload, retention_paths = _load_retention_report()
    storage_health = {
        "status": "PASS",
        "reasons": [],
        "evidence_paths_rel": retention_paths,
    }
    if retention_payload is None:
        storage_health["status"] = "ISSUE"
        storage_health["reasons"] = ["retention_report_missing_or_invalid"]
    else:
        safety = retention_payload.get("safety_checks", {}) if isinstance(
            retention_payload.get("safety_checks"), dict
        ) else {}
        candidates = retention_payload.get("candidates", [])
        if not safety.get("latest_pointers_protected", True) or not safety.get(
            "required_files_present", True
        ):
            storage_health["status"] = "BLOCKED"
            storage_health["reasons"] = ["retention_safety_checks_failed"]
        elif isinstance(candidates, list) and candidates:
            storage_health["status"] = "ISSUE"
            storage_health["reasons"] = ["retention_candidates_present"]

    if storage_health["status"] != "PASS":
        issues.append(
            {
                "id": "STORAGE_HEALTH",
                "severity": "WARN" if storage_health["status"] == "ISSUE" else "HIGH",
                "summary": f"Storage health {storage_health['status']}: "
                f"{', '.join(storage_health.get('reasons', []))}.",
                "evidence_paths_rel": storage_health.get("evidence_paths_rel", []),
                "suggested_actions": [
                    ACTION_RUN_RETENTION_REPORT,
                    ACTION_PRUNE_OLD_RUNS_SAFE,
                    ACTION_REBUILD_RECENT_INDEX,
                ],
            }
        )

    overtrading_payload = _safe_read_json(TRADE_ACTIVITY_REPORT_PATH)
    overtrading_status = "MISSING"
    overtrading_level = "DANGEROUS"
    overtrading_violations: list[str] = []
    overtrading_evidence = [to_repo_relative(TRADE_ACTIVITY_REPORT_PATH)]
    if overtrading_payload:
        overtrading_status = str(overtrading_payload.get("status") or "UNKNOWN")
        violations = overtrading_payload.get("violations", [])
        if isinstance(violations, list):
            overtrading_violations = [
                str(item.get("code", item)) if isinstance(item, dict) else str(item)
                for item in violations
            ]
        if overtrading_status == "PASS" and not overtrading_violations:
            overtrading_level = "SAFE"
            budget = overtrading_payload.get("budget", {})
            budget_values = budget.get("budget", {}) if isinstance(budget, dict) else {}
            max_trades = budget_values.get("max_trades_per_day")
            max_turnover = budget_values.get("max_turnover_per_day")
            min_seconds = budget_values.get("min_seconds_between_trades")
            trades_peak = overtrading_payload.get("trades_per_day_peak")
            turnover_gross = overtrading_payload.get("turnover_gross")
            min_between = overtrading_payload.get("min_seconds_between_trades")
            if isinstance(max_trades, (int, float)) and isinstance(trades_peak, (int, float)):
                if trades_peak >= float(max_trades) * 0.8:
                    overtrading_level = "CAUTION"
            if isinstance(max_turnover, (int, float)) and isinstance(turnover_gross, (int, float)):
                if turnover_gross >= float(max_turnover) * 0.8:
                    overtrading_level = "CAUTION"
            if isinstance(min_seconds, (int, float)) and isinstance(min_between, (int, float)):
                if min_between <= float(min_seconds) * 1.2:
                    overtrading_level = "CAUTION"
        else:
            overtrading_level = "DANGEROUS"

    if overtrading_level != "SAFE":
        issues.append(
            {
                "id": "OVERTRADING_GUARDRAILS",
                "severity": "HIGH" if overtrading_level == "DANGEROUS" else "WARN",
                "summary": f"Overtrading status {overtrading_level} (audit={overtrading_status}).",
                "evidence_paths_rel": overtrading_evidence,
                "suggested_actions": [ACTION_ENABLE_OVERTRADING_GUARDRAILS],
            }
        )

    if os.environ.get("PR30_INJECT_ISSUES") == "1":
        injected = [
            (ACTION_GEN_DOCTOR_REPORT, "Injected doctor report issue."),
            (ACTION_CLEAR_KILL_SWITCH, "Injected kill switch issue."),
            (ACTION_REPO_HYGIENE_FIX_SAFE, "Injected repo hygiene issue."),
            (ACTION_CLEAR_STALE_TEMP, "Injected stale temp issue."),
            (ACTION_ENSURE_RUNTIME_DIRS, "Injected runtime dir issue."),
            (ACTION_DIAG_RUNTIME_WRITE, "Injected runtime write issue."),
            (ACTION_ABS_PATH_SANITIZE_HINT, "Injected abs path issue."),
            (ACTION_ENABLE_GIT_HOOKS, "Injected git hooks issue."),
        ]
        for action_id, summary in injected:
            issues.append(
                {
                    "id": f"INJECT_{action_id}",
                    "severity": "WARN",
                    "summary": summary,
                    "evidence_paths_rel": [],
                    "suggested_actions": [action_id],
                }
            )

    git_status_lines, git_error = repo_hygiene.git_status_porcelain(include_ignored=True)
    git_clean = 1 if git_error is None and not git_status_lines else 0
    git_dirty_files = _collect_git_dirty_files(git_status_lines, MAX_GIT_DIRTY_FILES)
    repo_root_rel = "."
    report = {
        "ts_utc": now.isoformat(),
        "repo_root_rel": repo_root_rel,
        "repo_root_hash": _repo_root_hash(ROOT),
        "repo_root_detected": ROOT.name,
        "python_executable": _python_executable(),
        "python_version": sys.version.split()[0],
        "venv_detected": venv_detected,
        "kill_switch_present": kill_switch_present,
        "runtime_write_health": runtime_write_health,
        "git_status": {
            "clean": git_clean,
            "available": git_error is None,
            "error": git_error,
            "dirty_count": len(git_status_lines),
        },
        "git_dirty_files": git_dirty_files,
        "repo_hygiene_summary": {
            "status": hygiene.get("status", "UNKNOWN"),
            "tracked_modified_count": counts.get("tracked_modified", 0),
            "untracked_count": counts.get("untracked", 0),
            "runtime_artifact_count": counts.get("runtime_artifacts", 0),
            "unknown_count": counts.get("unknown", 0),
        },
        "storage_health": storage_health,
        "overtrading_activity": {
            "status": overtrading_status,
            "level": overtrading_level,
            "violations": overtrading_violations,
            "report_path": to_repo_relative(TRADE_ACTIVITY_REPORT_PATH),
        },
        "issues": issues,
    }
    return report


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Doctor report generator (SIM-only, read-only)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path for doctor_report.json")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_path = Path(args.output)
    try:
        report = build_report()
        write_report(report, output_path)
        return 0
    except Exception as exc:
        error_text = _sanitize_message(str(exc))
        fallback = {
            "ts_utc": _iso_now(),
            "repo_root_rel": ".",
            "repo_root_hash": _repo_root_hash(ROOT),
            "repo_root_detected": ROOT.name,
            "python_executable": _python_executable(),
            "python_version": sys.version.split()[0],
            "venv_detected": bool(os.environ.get("VIRTUAL_ENV") or (ROOT / ".venv").exists()),
            "kill_switch_present": any(path.exists() for path in KILL_SWITCH_PATHS),
            "runtime_write_health": {"status": "FAIL", "error": {"error_message": error_text}},
            "git_status": {"clean": 0, "available": False, "error": "doctor_report_failed", "dirty_count": 0},
            "git_dirty_files": [],
            "repo_hygiene_summary": {
                "status": "UNKNOWN",
                "tracked_modified_count": 0,
                "untracked_count": 0,
                "runtime_artifact_count": 0,
                "unknown_count": 0,
            },
            "issues": [
                {
                    "id": "DOCTOR_REPORT_ERROR",
                    "severity": "HIGH",
                    "summary": f"Doctor report failed: {error_text}",
                    "evidence_paths_rel": [],
                    "suggested_actions": [ACTION_GEN_DOCTOR_REPORT],
                }
            ],
        }
        write_report(fallback, output_path)
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
