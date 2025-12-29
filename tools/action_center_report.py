from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.git_baseline_probe import probe_baseline
from tools import doctor_report
from tools import git_hygiene_fix
from tools.overtrading_budget import DEFAULT_BUDGET, load_overtrading_budget
from tools.paths import runtime_dir, to_repo_relative

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
RUNS_DIR = LOGS_DIR / "train_runs"
LATEST_DIR = RUNS_DIR / "_latest"
PROGRESS_INDEX_PATH = RUNS_DIR / "progress_index.json"
STATE_PATH = LOGS_DIR / "train_service" / "state.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "action_center_report.json"
DOCTOR_REPORT_PATH = ROOT / "artifacts" / "doctor_report.json"
DOCTOR_DIAG_OUTPUT = ROOT / "artifacts" / "doctor_runtime_write.json"
ABS_PATH_HINT_OUTPUT = ROOT / "artifacts" / "abs_path_sanitize_hint.json"
SUPERVISOR_SCRIPT = ROOT / "tools" / "supervisor.py"
PROGRESS_INDEX_SCRIPT = ROOT / "tools" / "progress_index.py"
RECENT_RUNS_INDEX_SCRIPT = ROOT / "tools" / "recent_runs_index.py"
GIT_HYGIENE_PLAN_PATH = ROOT / "artifacts" / "git_hygiene_fix_plan.json"
GIT_HYGIENE_RESULT_PATH = ROOT / "artifacts" / "git_hygiene_fix_result.json"
GIT_HYGIENE_REVIEW_PATH = ROOT / "artifacts" / "git_hygiene_review.json"

LATEST_POINTERS = [
    "candidates_latest.json",
    "tournament_latest.json",
    "promotion_decision_latest.json",
    "policy_history_latest.json",
    "progress_judge_latest.json",
]

CONFIRM_TOKENS = {
    "CLEAR_KILL_SWITCH": "CLEAR",
    "ACTION_REBUILD_PROGRESS_INDEX": "REBUILD",
    "ACTION_RESTART_SERVICES_SIM_ONLY": "RESTART",
    "GEN_DOCTOR_REPORT": "RUN",
    "REPO_HYGIENE_FIX_SAFE": "HYGIENE",
    "CLEAR_STALE_TEMP": "CLEAN",
    "ENSURE_RUNTIME_DIRS": "MKDIR",
    "DIAG_RUNTIME_WRITE": "DIAG",
    "ABS_PATH_SANITIZE_HINT": "SANITIZE",
    "ENABLE_GIT_HOOKS": "HOOKS",
    "RUN_RETENTION_REPORT": "REPORT",
    "PRUNE_OLD_RUNS_SAFE": "PRUNE",
    "REBUILD_RECENT_INDEX": "INDEX",
    "FIX_GIT_RED_SAFE": "GITSAFE",
    "REVIEW_GIT_DIRTY": "REVIEW",
    "ENABLE_OVERTRADING_GUARDRAILS_SAFE": "GUARDRAILS",
}

ACTION_DEFINITIONS = {
    "CLEAR_KILL_SWITCH": {
        "title": "Clear kill switch (SIM-only)",
        "confirmation_token": CONFIRM_TOKENS["CLEAR_KILL_SWITCH"],
        "safety_notes": "SIM-only. Removes local kill switch files and does not place trades.",
        "effect_summary": "Clears kill switch files via supervisor clear-kill-switch.",
        "risk_level": "CAUTION",
    },
    "ACTION_REBUILD_PROGRESS_INDEX": {
        "title": "Rebuild progress index",
        "confirmation_token": CONFIRM_TOKENS["ACTION_REBUILD_PROGRESS_INDEX"],
        "safety_notes": "SIM-only. Regenerates Logs/train_runs/progress_index.json from local files.",
        "effect_summary": "Runs tools/progress_index.py to refresh the progress index.",
        "risk_level": "CAUTION",
    },
    "ACTION_RESTART_SERVICES_SIM_ONLY": {
        "title": "Restart SIM services",
        "confirmation_token": CONFIRM_TOKENS["ACTION_RESTART_SERVICES_SIM_ONLY"],
        "safety_notes": "SIM-only. Restarts local supervisor-managed services; no broker access.",
        "effect_summary": "Stops and starts supervisor services (quotes/alerts).",
        "risk_level": "CAUTION",
    },
    "GEN_DOCTOR_REPORT": {
        "title": "Generate Doctor report",
        "confirmation_token": CONFIRM_TOKENS["GEN_DOCTOR_REPORT"],
        "safety_notes": "SIM-only. Writes artifacts/doctor_report.json for diagnostics.",
        "effect_summary": "Runs tools.doctor_report to capture health evidence.",
        "risk_level": "SAFE",
    },
    "REPO_HYGIENE_FIX_SAFE": {
        "title": "Repo hygiene fix (safe)",
        "confirmation_token": CONFIRM_TOKENS["REPO_HYGIENE_FIX_SAFE"],
        "safety_notes": "SIM-only. Restores tracked runtime artifacts and removes untracked runtime files.",
        "effect_summary": "Runs python -m tools.repo_hygiene fix --mode safe.",
        "risk_level": "CAUTION",
    },
    "CLEAR_STALE_TEMP": {
        "title": "Clear stale temp files",
        "confirmation_token": CONFIRM_TOKENS["CLEAR_STALE_TEMP"],
        "safety_notes": "SIM-only. Deletes stale *.tmp files older than the Doctor threshold.",
        "effect_summary": "Removes stale temp files from Logs/runtime and Logs/.",
        "risk_level": "CAUTION",
    },
    "ENSURE_RUNTIME_DIRS": {
        "title": "Ensure runtime directories",
        "confirmation_token": CONFIRM_TOKENS["ENSURE_RUNTIME_DIRS"],
        "safety_notes": "SIM-only. Creates runtime directories if missing.",
        "effect_summary": "Creates Logs/runtime, Logs/train_service, and artifacts directories.",
        "risk_level": "SAFE",
    },
    "DIAG_RUNTIME_WRITE": {
        "title": "Diagnose runtime write",
        "confirmation_token": CONFIRM_TOKENS["DIAG_RUNTIME_WRITE"],
        "safety_notes": "SIM-only. Re-runs runtime write checks and stores results.",
        "effect_summary": "Writes artifacts/doctor_runtime_write.json for evidence.",
        "risk_level": "SAFE",
    },
    "ABS_PATH_SANITIZE_HINT": {
        "title": "Generate absolute-path sanitization hints",
        "confirmation_token": CONFIRM_TOKENS["ABS_PATH_SANITIZE_HINT"],
        "safety_notes": "SIM-only. Writes a sanitized-paths guidance artifact.",
        "effect_summary": "Writes artifacts/abs_path_sanitize_hint.json with guidance.",
        "risk_level": "SAFE",
    },
    "ENABLE_GIT_HOOKS": {
        "title": "Enable git hooks (best effort)",
        "confirmation_token": CONFIRM_TOKENS["ENABLE_GIT_HOOKS"],
        "safety_notes": "SIM-only. Best-effort enable githooks for repo hygiene.",
        "effect_summary": "Runs scripts/enable_githooks.* if available.",
        "risk_level": "SAFE",
    },
    "RUN_RETENTION_REPORT": {
        "title": "Run retention report",
        "confirmation_token": CONFIRM_TOKENS["RUN_RETENTION_REPORT"],
        "safety_notes": "SIM-only. Generates retention report for storage health evidence.",
        "effect_summary": "Runs python -m tools.retention_engine report.",
        "risk_level": "SAFE",
    },
    "PRUNE_OLD_RUNS_SAFE": {
        "title": "Prune old runs (safe)",
        "confirmation_token": CONFIRM_TOKENS["PRUNE_OLD_RUNS_SAFE"],
        "safety_notes": "SIM-only. Conservative retention prune with safety checks.",
        "effect_summary": "Runs python -m tools.retention_engine prune --mode safe.",
        "risk_level": "SAFE",
    },
    "REBUILD_RECENT_INDEX": {
        "title": "Rebuild recent runs index",
        "confirmation_token": CONFIRM_TOKENS["REBUILD_RECENT_INDEX"],
        "safety_notes": "SIM-only. Rebuilds Logs/train_runs/recent_runs_index.json.",
        "effect_summary": "Runs python -m tools.recent_runs_index.",
        "risk_level": "SAFE",
    },
    "FIX_GIT_RED_SAFE": {
        "title": "Fix Git Red (Safe)",
        "confirmation_token": CONFIRM_TOKENS["FIX_GIT_RED_SAFE"],
        "safety_notes": "SIM-only. Restores tracked runtime artifacts and removes untracked runtime files only.",
        "effect_summary": "Applies safe git hygiene fixes and writes evidence artifacts.",
        "risk_level": "SAFE",
    },
    "REVIEW_GIT_DIRTY": {
        "title": "Review unknown git changes",
        "confirmation_token": CONFIRM_TOKENS["REVIEW_GIT_DIRTY"],
        "safety_notes": "SIM-only. Generates guidance for unknown git changes; no auto-apply.",
        "effect_summary": "Writes a review guidance artifact for manual inspection.",
        "risk_level": "CAUTION",
    },
    "ENABLE_OVERTRADING_GUARDRAILS_SAFE": {
        "title": "Enable Overtrading Guardrails (Safe Defaults)",
        "confirmation_token": CONFIRM_TOKENS["ENABLE_OVERTRADING_GUARDRAILS_SAFE"],
        "safety_notes": "SIM-only. Writes runtime overtrading budget config; no broker access.",
        "effect_summary": "Copies Data/overtrading_budget.json into Logs/runtime/overtrading_budget.json.",
        "risk_level": "SAFE",
    },
}


@dataclass
class ActionExecutionResult:
    action_id: str
    success: bool
    message: str
    details: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _now().isoformat()


def _relpath(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _git_output(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _repo_ref() -> dict[str, str]:
    return {
        "git_commit_short": _git_output(["rev-parse", "--short", "HEAD"]) or "unknown",
        "branch_if_known": _git_output(["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown",
    }


def _parse_iso(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _event_path(now: datetime) -> Path:
    return LOGS_DIR / f"events_{now:%Y-%m-%d}.jsonl"


def write_event(event_type: str, message: str, severity: str = "INFO", **extra: Any) -> dict[str, Any]:
    now = _now()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "ts_utc": now.isoformat(),
        "event_type": event_type,
        "severity": severity,
        "message": message,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    path = _event_path(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    payload["events_path"] = _relpath(path)
    return payload


def confirm_token_is_valid(confirm_token: str, expected: str) -> bool:
    if not confirm_token:
        return False
    return confirm_token.strip() == expected


def _ensure_not_ci() -> None:
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("Action execution is disabled in CI environments.")


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _sanitize_command(command: list[str]) -> list[str]:
    sanitized: list[str] = []
    for arg in command:
        try:
            path = Path(arg)
        except Exception:
            sanitized.append(str(arg))
            continue
        if path.is_absolute():
            rel = _relpath(path)
            if rel == path.as_posix():
                sanitized.append("<abs_path>")
            else:
                sanitized.append(rel)
        else:
            sanitized.append(str(arg))
    return sanitized


def _execute_clear_kill_switch() -> ActionExecutionResult:
    proc = _run_command([sys.executable, str(SUPERVISOR_SCRIPT), "clear-kill-switch"])
    details = {
        "command": _sanitize_command(list(proc.args)),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    if proc.returncode == 0:
        details["changes_made"] = [
            _relpath(ROOT / "Data" / "KILL_SWITCH"),
            _relpath(LOGS_DIR / "train_service" / "KILL_SWITCH"),
        ]
        write_event(
            "KILL_SWITCH_CLEARED",
            "Action Center cleared kill switch",
            action_id="CLEAR_KILL_SWITCH",
        )
        return ActionExecutionResult("CLEAR_KILL_SWITCH", True, "kill switch cleared", details)
    write_event(
        "KILL_SWITCH_CLEAR_FAILED",
        "Action Center failed to clear kill switch",
        severity="ERROR",
        action_id="CLEAR_KILL_SWITCH",
    )
    return ActionExecutionResult("CLEAR_KILL_SWITCH", False, "kill switch clear failed", details)


def _execute_rebuild_progress_index() -> ActionExecutionResult:
    proc = _run_command([sys.executable, str(PROGRESS_INDEX_SCRIPT)])
    details = {
        "command": _sanitize_command(list(proc.args)),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    if proc.returncode == 0:
        details["changes_made"] = [_relpath(PROGRESS_INDEX_PATH)]
        write_event(
            "PROGRESS_INDEX_REBUILT",
            "Action Center rebuilt progress index",
            action_id="ACTION_REBUILD_PROGRESS_INDEX",
        )
        return ActionExecutionResult("ACTION_REBUILD_PROGRESS_INDEX", True, "progress index rebuilt", details)
    write_event(
        "PROGRESS_INDEX_REBUILD_FAILED",
        "Action Center failed to rebuild progress index",
        severity="ERROR",
        action_id="ACTION_REBUILD_PROGRESS_INDEX",
    )
    return ActionExecutionResult("ACTION_REBUILD_PROGRESS_INDEX", False, "progress index rebuild failed", details)


def _execute_restart_services() -> ActionExecutionResult:
    stop_proc = _run_command([sys.executable, str(SUPERVISOR_SCRIPT), "stop"])
    start_proc = None
    success = stop_proc.returncode == 0
    if success:
        start_proc = _run_command([sys.executable, str(SUPERVISOR_SCRIPT), "start"])
        success = start_proc.returncode == 0

    details = {
        "stop": {
            "command": _sanitize_command(list(stop_proc.args)),
            "returncode": stop_proc.returncode,
            "stdout": stop_proc.stdout,
            "stderr": stop_proc.stderr,
        },
        "start": {
            "command": _sanitize_command(list(start_proc.args)) if start_proc else [],
            "returncode": start_proc.returncode if start_proc else None,
            "stdout": start_proc.stdout if start_proc else "",
            "stderr": start_proc.stderr if start_proc else "",
        },
    }
    if success:
        write_event(
            "SERVICES_RESTARTED",
            "Action Center restarted supervisor services",
            action_id="ACTION_RESTART_SERVICES_SIM_ONLY",
        )
        return ActionExecutionResult("ACTION_RESTART_SERVICES_SIM_ONLY", True, "services restarted", details)
    write_event(
        "SERVICES_RESTART_FAILED",
        "Action Center failed to restart supervisor services",
        severity="ERROR",
        action_id="ACTION_RESTART_SERVICES_SIM_ONLY",
    )
    return ActionExecutionResult("ACTION_RESTART_SERVICES_SIM_ONLY", False, "service restart failed", details)


def _execute_generate_doctor_report() -> ActionExecutionResult:
    report = doctor_report.build_report()
    doctor_report.write_report(report, DOCTOR_REPORT_PATH)
    details = {
        "output_path": _relpath(DOCTOR_REPORT_PATH),
        "issues_count": len(report.get("issues", [])),
        "changes_made": [_relpath(DOCTOR_REPORT_PATH)],
    }
    return ActionExecutionResult("GEN_DOCTOR_REPORT", True, "doctor report generated", details)


def _execute_repo_hygiene_fix_safe() -> ActionExecutionResult:
    proc = _run_command([sys.executable, "-m", "tools.repo_hygiene", "fix", "--mode", "safe"])
    details = {
        "command": _sanitize_command(list(proc.args)),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    success = proc.returncode == 0
    message = "repo hygiene fix completed" if success else "repo hygiene fix failed"
    return ActionExecutionResult("REPO_HYGIENE_FIX_SAFE", success, message, details)


def _execute_clear_stale_temp() -> ActionExecutionResult:
    stale_files = doctor_report.find_stale_temp_files(
        [LOGS_DIR, LOGS_DIR / "runtime"], doctor_report.TEMP_FILE_THRESHOLD_SECONDS
    )
    removed: list[str] = []
    failures: list[str] = []
    for path in stale_files:
        try:
            path.unlink()
            removed.append(_relpath(path))
        except Exception:
            failures.append(_relpath(path))
    success = not failures
    details = {"removed": removed, "failed": failures}
    details["changes_made"] = removed
    message = f"removed {len(removed)} stale temp files" if success else "failed to remove stale temp files"
    return ActionExecutionResult("CLEAR_STALE_TEMP", success, message, details)


def _execute_ensure_runtime_dirs() -> ActionExecutionResult:
    targets = [LOGS_DIR / "runtime", LOGS_DIR / "train_service", ROOT / "artifacts"]
    created: list[str] = []
    for path in targets:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(_relpath(path))
    details = {"created": created, "targets": [_relpath(path) for path in targets], "changes_made": created}
    return ActionExecutionResult("ENSURE_RUNTIME_DIRS", True, "runtime directories ensured", details)


def _execute_diag_runtime_write() -> ActionExecutionResult:
    result = doctor_report.runtime_write_check(LOGS_DIR / "runtime")
    payload = {"ts_utc": _iso_now(), "runtime_write_health": result}
    DOCTOR_DIAG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DOCTOR_DIAG_OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    details = {
        "output_path": _relpath(DOCTOR_DIAG_OUTPUT),
        "status": result.get("status"),
        "changes_made": [_relpath(DOCTOR_DIAG_OUTPUT)],
    }
    success = result.get("status") == "PASS"
    message = "runtime write check passed" if success else "runtime write check failed"
    return ActionExecutionResult("DIAG_RUNTIME_WRITE", success, message, details)


def _execute_abs_path_sanitize_hint() -> ActionExecutionResult:
    artifact_candidates = [
        ROOT / "artifacts" / "action_center_report.json",
        ROOT / "artifacts" / "action_center_apply_result.json",
        ROOT / "artifacts" / "proof_summary.json",
        ROOT / "artifacts" / "gates.log",
        ROOT / "artifacts" / "ci_job_summary.md",
    ]
    leaked_paths = doctor_report.scan_absolute_paths(artifact_candidates)
    payload = {
        "ts_utc": _iso_now(),
        "summary": "Check artifacts for absolute Windows paths (e.g., C:\\...).",
        "detected_artifacts": [_relpath(path) for path in leaked_paths],
        "guidance": [
            "Prefer repo-relative paths in artifacts.",
            "Avoid persisting user home directories in logs.",
            "Redact absolute paths in reports before sharing.",
        ],
    }
    ABS_PATH_HINT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ABS_PATH_HINT_OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    details = {"output_path": _relpath(ABS_PATH_HINT_OUTPUT), "changes_made": [_relpath(ABS_PATH_HINT_OUTPUT)]}
    return ActionExecutionResult("ABS_PATH_SANITIZE_HINT", True, "abs path hint written", details)


def _execute_enable_githooks() -> ActionExecutionResult:
    script = ROOT / "scripts" / "enable_githooks.sh"
    if os.name == "nt":
        script = ROOT / "scripts" / "enable_githooks.ps1"
    if not script.exists():
        return ActionExecutionResult(
            "ENABLE_GIT_HOOKS",
            False,
            "githook enable script unavailable",
            {"refused": True, "reason": "script missing"},
        )
    proc = _run_command([str(script)])
    details = {
        "command": _sanitize_command(list(proc.args)),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    success = proc.returncode == 0
    message = "githooks enabled" if success else "githooks enable failed"
    if success:
        details["changes_made"] = [_relpath(script)]
    return ActionExecutionResult("ENABLE_GIT_HOOKS", success, message, details)


def _execute_retention_report() -> ActionExecutionResult:
    proc = _run_command([sys.executable, "-m", "tools.retention_engine", "report"])
    details = {
        "command": _sanitize_command(list(proc.args)),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    changes = []
    from tools.retention_engine import RETENTION_REPORT_RUNTIME, RETENTION_REPORT_ARTIFACTS

    if RETENTION_REPORT_RUNTIME.exists():
        changes.append(_relpath(RETENTION_REPORT_RUNTIME))
    if RETENTION_REPORT_ARTIFACTS.exists():
        changes.append(_relpath(RETENTION_REPORT_ARTIFACTS))
    details["changes_made"] = changes
    success = proc.returncode == 0
    message = "retention report generated" if success else "retention report failed"
    return ActionExecutionResult("RUN_RETENTION_REPORT", success, message, details)


def _execute_retention_prune_safe() -> ActionExecutionResult:
    proc = _run_command([sys.executable, "-m", "tools.retention_engine", "prune", "--mode", "safe"])
    details = {
        "command": _sanitize_command(list(proc.args)),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    from tools.retention_engine import PRUNE_PLAN_RUNTIME, PRUNE_RESULT_RUNTIME

    changes = []
    if PRUNE_PLAN_RUNTIME.exists():
        changes.append(_relpath(PRUNE_PLAN_RUNTIME))
    if PRUNE_RESULT_RUNTIME.exists():
        changes.append(_relpath(PRUNE_RESULT_RUNTIME))
    details["changes_made"] = changes
    success = proc.returncode == 0
    message = "retention prune completed" if success else "retention prune failed"
    return ActionExecutionResult("PRUNE_OLD_RUNS_SAFE", success, message, details)


def _execute_rebuild_recent_index() -> ActionExecutionResult:
    proc = _run_command([sys.executable, str(RECENT_RUNS_INDEX_SCRIPT)])
    details = {
        "command": _sanitize_command(list(proc.args)),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    index_path = RUNS_DIR / "recent_runs_index.json"
    latest_path = RUNS_DIR / "_latest" / "recent_runs_index_latest.json"
    changes = []
    if index_path.exists():
        changes.append(_relpath(index_path))
    if latest_path.exists():
        changes.append(_relpath(latest_path))
    details["changes_made"] = changes
    success = proc.returncode == 0
    message = "recent runs index rebuilt" if success else "recent runs index rebuild failed"
    return ActionExecutionResult("REBUILD_RECENT_INDEX", success, message, details)


def _execute_fix_git_red_safe() -> ActionExecutionResult:
    plan = git_hygiene_fix.build_plan()
    git_hygiene_fix.write_plan(plan, GIT_HYGIENE_PLAN_PATH)
    if not plan.get("git_available", False):
        details = {
            "plan_path": _relpath(GIT_HYGIENE_PLAN_PATH),
            "refused": True,
            "reason": plan.get("git_error") or "git unavailable",
        }
        write_event(
            "GIT_HYGIENE_FIX_REFUSED",
            "Action Center refused git hygiene fix (git unavailable).",
            severity="ERROR",
            action_id="FIX_GIT_RED_SAFE",
            evidence_paths=[_relpath(GIT_HYGIENE_PLAN_PATH)],
        )
        return ActionExecutionResult("FIX_GIT_RED_SAFE", False, "git unavailable; refused", details)

    result = git_hygiene_fix.apply_fix(plan, dry_run=False)
    git_hygiene_fix.write_result(result, GIT_HYGIENE_RESULT_PATH)
    details = {
        "plan_path": _relpath(GIT_HYGIENE_PLAN_PATH),
        "result_path": _relpath(GIT_HYGIENE_RESULT_PATH),
        "status": result.get("status"),
        "changes_made": result.get("changes_made", []),
        "stdout": "",
        "stderr": "",
    }
    success = result.get("status") == "PASS"
    write_event(
        "GIT_HYGIENE_FIX_APPLIED",
        "Action Center applied git hygiene fix (safe).",
        severity="INFO" if success else "ERROR",
        action_id="FIX_GIT_RED_SAFE",
        result_status=result.get("status"),
        evidence_paths=[_relpath(GIT_HYGIENE_PLAN_PATH), _relpath(GIT_HYGIENE_RESULT_PATH)],
    )
    message = "git hygiene fix applied" if success else "git hygiene fix failed"
    return ActionExecutionResult("FIX_GIT_RED_SAFE", success, message, details)


def _execute_review_git_dirty() -> ActionExecutionResult:
    plan = git_hygiene_fix.build_plan()
    payload = {
        "ts_utc": _iso_now(),
        "summary": "Unknown git changes detected; review manually.",
        "unknown_paths": plan.get("unknown_paths", []),
        "git_status_before": plan.get("git_status_before", {}),
        "guidance": [
            "Run: git status --porcelain --untracked-files=all",
            "Inspect unknown paths and decide whether to keep, ignore, or revert.",
            "Use git restore <path> for tracked changes; git clean -fd only if you are sure.",
        ],
    }
    GIT_HYGIENE_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    GIT_HYGIENE_REVIEW_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    details = {
        "output_path": _relpath(GIT_HYGIENE_REVIEW_PATH),
        "changes_made": [_relpath(GIT_HYGIENE_REVIEW_PATH)],
    }
    write_event(
        "GIT_HYGIENE_REVIEW_READY",
        "Action Center wrote git hygiene review guidance.",
        action_id="REVIEW_GIT_DIRTY",
        evidence_paths=[_relpath(GIT_HYGIENE_REVIEW_PATH)],
    )
    return ActionExecutionResult("REVIEW_GIT_DIRTY", True, "review guidance written", details)


def _execute_enable_overtrading_guardrails() -> ActionExecutionResult:
    budget_payload = load_overtrading_budget()
    seed_budget = budget_payload.get("budget") if isinstance(budget_payload, dict) else None
    if not isinstance(seed_budget, dict):
        seed_budget = dict(DEFAULT_BUDGET)
    runtime_path = runtime_dir() / "overtrading_budget.json"
    payload = dict(seed_budget)
    payload["enabled_utc"] = _iso_now()
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    details = {
        "output_path": _relpath(runtime_path),
        "changes_made": [_relpath(runtime_path)],
        "source_seed": to_repo_relative(ROOT / "Data" / "overtrading_budget.json"),
    }
    return ActionExecutionResult(
        "ENABLE_OVERTRADING_GUARDRAILS_SAFE",
        True,
        "overtrading guardrails enabled",
        details,
    )


def _execute_action(action_id: str) -> ActionExecutionResult:
    if action_id == "CLEAR_KILL_SWITCH":
        return _execute_clear_kill_switch()
    if action_id == "ACTION_REBUILD_PROGRESS_INDEX":
        return _execute_rebuild_progress_index()
    if action_id == "ACTION_RESTART_SERVICES_SIM_ONLY":
        return _execute_restart_services()
    if action_id == "GEN_DOCTOR_REPORT":
        return _execute_generate_doctor_report()
    if action_id == "REPO_HYGIENE_FIX_SAFE":
        return _execute_repo_hygiene_fix_safe()
    if action_id == "CLEAR_STALE_TEMP":
        return _execute_clear_stale_temp()
    if action_id == "ENSURE_RUNTIME_DIRS":
        return _execute_ensure_runtime_dirs()
    if action_id == "DIAG_RUNTIME_WRITE":
        return _execute_diag_runtime_write()
    if action_id == "ABS_PATH_SANITIZE_HINT":
        return _execute_abs_path_sanitize_hint()
    if action_id == "ENABLE_GIT_HOOKS":
        return _execute_enable_githooks()
    if action_id == "RUN_RETENTION_REPORT":
        return _execute_retention_report()
    if action_id == "PRUNE_OLD_RUNS_SAFE":
        return _execute_retention_prune_safe()
    if action_id == "REBUILD_RECENT_INDEX":
        return _execute_rebuild_recent_index()
    if action_id == "FIX_GIT_RED_SAFE":
        return _execute_fix_git_red_safe()
    if action_id == "REVIEW_GIT_DIRTY":
        return _execute_review_git_dirty()
    if action_id == "ENABLE_OVERTRADING_GUARDRAILS_SAFE":
        return _execute_enable_overtrading_guardrails()
    raise ValueError(f"Unknown action_id: {action_id}")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _load_doctor_report() -> dict[str, Any] | None:
    return _load_json(DOCTOR_REPORT_PATH)


def _doctor_issue_to_action_center(issue: dict[str, Any]) -> dict[str, Any]:
    evidence = issue.get("evidence_paths_rel", [])
    actions = issue.get("suggested_actions", [])
    return {
        "code": str(issue.get("id", "DOCTOR_ISSUE")),
        "severity": str(issue.get("severity", "INFO")),
        "summary": str(issue.get("summary", "")),
        "evidence_paths": list(evidence) if isinstance(evidence, list) else [],
        "recommended_actions": list(actions) if isinstance(actions, list) else [],
    }


def _latest_run_complete(runs_root: Path) -> Path | None:
    candidates = sorted(runs_root.glob("**/run_complete.json"))
    if not candidates:
        return None
    latest = None
    latest_mtime = -1.0
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > latest_mtime:
            latest = path
            latest_mtime = mtime
    return latest


def _collect_missing_latest_pointers() -> list[Path]:
    missing: list[Path] = []
    for name in LATEST_POINTERS:
        path = LATEST_DIR / name
        if not path.exists():
            missing.append(path)
    return missing


def _validate_pointer_payload(path: Path) -> tuple[bool, list[str]]:
    payload = _load_json(path)
    if payload is None:
        return False, ["parse_failed"]
    missing = [key for key in ("schema_version", "created_utc", "run_id") if key not in payload]
    return not missing, [f"missing:{','.join(missing)}"] if missing else []


def _service_heartbeat_issue(now: datetime) -> tuple[bool, list[str], str]:
    payload = _load_json(STATE_PATH)
    if payload is None:
        return False, [], ""
    raw = payload.get("last_heartbeat_ts")
    heartbeat = _parse_iso(raw)
    if heartbeat is None:
        return True, [_relpath(STATE_PATH)], "last_heartbeat_ts missing or invalid"
    age = int((now - heartbeat).total_seconds())
    if age > 180:
        return True, [_relpath(STATE_PATH)], f"heartbeat age {age}s"
    return False, [_relpath(STATE_PATH)], ""


def _build_recommended_actions(action_evidence: dict[str, set[str]] | None = None) -> list[dict[str, Any]]:
    action_evidence = action_evidence or {action_id: set() for action_id in ACTION_DEFINITIONS}
    recommended_actions: list[dict[str, Any]] = []
    for action_id in ACTION_DEFINITIONS:
        action = ACTION_DEFINITIONS[action_id]
        risk_level = str(action.get("risk_level", "SAFE")).upper()
        recommended_actions.append(
            {
                "action_id": action_id,
                "title": action["title"],
                "requires_typed_confirmation": risk_level != "SAFE",
                "confirmation_token": action["confirmation_token"],
                "safety_notes": action["safety_notes"],
                "effect_summary": action["effect_summary"],
                "risk_level": risk_level,
                "related_evidence_paths": sorted(action_evidence.get(action_id, set())),
            }
        )
    return recommended_actions


def _severity_rank(severity: str) -> int:
    order = {"HIGH": 3, "WARN": 2, "INFO": 1, "OK": 0}
    return order.get(severity.upper(), 0)


def _build_action_rows(
    issues: list[dict[str, Any]],
    now: datetime,
    action_evidence: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    action_evidence = action_evidence or {action_id: set() for action_id in ACTION_DEFINITIONS}
    rows: list[dict[str, Any]] = []
    for action_id, action in ACTION_DEFINITIONS.items():
        matching = [
            issue
            for issue in issues
            if isinstance(issue, dict) and action_id in issue.get("recommended_actions", [])
        ]
        severity = "INFO"
        summary = "No active issues detected."
        if matching:
            ranked = sorted(
                matching,
                key=lambda entry: _severity_rank(str(entry.get("severity", "INFO"))),
                reverse=True,
            )
            top = ranked[0]
            severity = str(top.get("severity", "INFO")).upper()
            summary = str(top.get("summary", summary))
        recommended_command = (
            f"python -m tools.action_center_apply --action-id {action_id} "
            f"--confirm {action['confirmation_token']}"
        )
        evidence_paths = sorted(action_evidence.get(action_id, set()))
        rows.append(
            {
                "action_id": action_id,
                "severity": severity,
                "risk_level": action.get("risk_level", "SAFE"),
                "summary": summary,
                "recommended_command": recommended_command,
                "evidence_paths": evidence_paths,
                "last_seen_ts_utc": now.isoformat(),
            }
        )
    return rows


def build_report() -> dict[str, Any]:
    now = _now()
    environment_notes: list[dict[str, str]] = []
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        environment_notes.append(
            {"code": "ui_display_unavailable", "detail": "DISPLAY not set for GUI rendering."}
        )
    if not os.environ.get("VIRTUAL_ENV") and not (ROOT / ".venv").exists():
        environment_notes.append(
            {"code": "venv_unavailable", "detail": "No active virtualenv detected."}
        )
    baseline = probe_baseline()
    if baseline.get("status") != "AVAILABLE":
        environment_notes.append(
            {
                "code": "baseline_unavailable",
                "detail": f"baseline probe status={baseline.get('status')} details={baseline.get('details')}",
            }
        )

    issues: list[dict[str, Any]] = []

    kill_switch_paths = [ROOT / "Data" / "KILL_SWITCH", LOGS_DIR / "train_service" / "KILL_SWITCH"]
    present_kill_switches = [path for path in kill_switch_paths if path.exists()]
    if present_kill_switches:
        issues.append(
            {
                "code": "KILL_SWITCH_PRESENT",
                "severity": "HIGH",
                "summary": "Kill switch file present; training services are halted.",
                "evidence_paths": sorted({_relpath(path) for path in present_kill_switches}),
                "recommended_actions": ["CLEAR_KILL_SWITCH"],
            }
        )

    latest_run_complete = _latest_run_complete(RUNS_DIR)
    if latest_run_complete is None:
        issues.append(
            {
                "code": "NO_COMPLETE_RUNS",
                "severity": "WARN",
                "summary": "No run_complete.json artifacts detected. No safe auto-action available.",
                "evidence_paths": [_relpath(RUNS_DIR)],
                "recommended_actions": [],
            }
        )

    if not PROGRESS_INDEX_PATH.exists():
        issues.append(
            {
                "code": "PROGRESS_INDEX_MISSING",
                "severity": "WARN",
                "summary": "progress_index.json missing; UI summaries may be incomplete.",
                "evidence_paths": [_relpath(PROGRESS_INDEX_PATH)],
                "recommended_actions": ["ACTION_REBUILD_PROGRESS_INDEX"],
            }
        )
    elif latest_run_complete:
        try:
            index_mtime = PROGRESS_INDEX_PATH.stat().st_mtime
            run_mtime = latest_run_complete.stat().st_mtime
        except OSError:
            index_mtime = None
            run_mtime = None
        if index_mtime is not None and run_mtime is not None and index_mtime + 60 < run_mtime:
            issues.append(
                {
                    "code": "PROGRESS_INDEX_STALE",
                    "severity": "WARN",
                    "summary": "progress_index.json appears stale versus latest run_complete.json.",
                    "evidence_paths": sorted({_relpath(PROGRESS_INDEX_PATH), _relpath(latest_run_complete)}),
                    "recommended_actions": ["ACTION_REBUILD_PROGRESS_INDEX"],
                }
            )

    missing_latest = _collect_missing_latest_pointers()
    if missing_latest:
        missing_names = ", ".join(path.name for path in missing_latest)
        issues.append(
            {
                "code": "LATEST_POINTERS_MISSING",
                "severity": "WARN",
                "summary": f"Latest pointer(s) missing: {missing_names}.",
                "evidence_paths": sorted({_relpath(path) for path in missing_latest}),
                "recommended_actions": ["ACTION_RESTART_SERVICES_SIM_ONLY"],
            }
        )

    pointer_run_ids: dict[str, str] = {}
    pointer_invalid: list[str] = []
    for name in LATEST_POINTERS:
        path = LATEST_DIR / name
        if not path.exists():
            continue
        valid, warnings = _validate_pointer_payload(path)
        if not valid:
            pointer_invalid.append(f"{name}:{'|'.join(warnings)}")
            continue
        payload = _load_json(path)
        run_id = payload.get("run_id") if payload else None
        if run_id:
            pointer_run_ids[name] = str(run_id)

    if pointer_invalid:
        issues.append(
            {
                "code": "LATEST_POINTER_INVALID",
                "severity": "WARN",
                "summary": "Latest pointer JSON invalid: " + "; ".join(pointer_invalid),
                "evidence_paths": sorted({_relpath(LATEST_DIR / name.split(":", 1)[0]) for name in pointer_invalid}),
                "recommended_actions": ["ACTION_RESTART_SERVICES_SIM_ONLY"],
            }
        )

    if len(set(pointer_run_ids.values())) > 1:
        issues.append(
            {
                "code": "LATEST_POINTER_INCONSISTENT",
                "severity": "WARN",
                "summary": "Latest pointers disagree on run_id.",
                "evidence_paths": sorted({_relpath(LATEST_DIR / name) for name in pointer_run_ids}),
                "recommended_actions": ["ACTION_RESTART_SERVICES_SIM_ONLY"],
            }
        )

    judge_paths = [
        LATEST_DIR / "progress_judge_latest.json",
        RUNS_DIR / "progress_judge" / "latest.json",
    ]
    judge_available = [path for path in judge_paths if path.exists()]
    if not judge_available:
        issues.append(
            {
                "code": "JUDGE_ARTIFACT_MISSING",
                "severity": "WARN",
                "summary": "Progress judge artifacts missing. No safe auto-action available.",
                "evidence_paths": [_relpath(path) for path in judge_paths],
                "recommended_actions": [],
            }
        )
    else:
        judge_path = judge_available[0]
        payload = _load_json(judge_path)
        required = {"schema_version", "created_utc", "run_id", "recommendation", "scores", "trend", "risk_metrics"}
        if payload is None:
            issues.append(
                {
                    "code": "JUDGE_ARTIFACT_INVALID",
                    "severity": "WARN",
                    "summary": "Progress judge artifact unreadable.",
                    "evidence_paths": [_relpath(judge_path)],
                    "recommended_actions": ["ACTION_RESTART_SERVICES_SIM_ONLY"],
                }
            )
        else:
            missing = sorted(required.difference(payload.keys()))
            if missing:
                issues.append(
                    {
                        "code": "JUDGE_ARTIFACT_INCOMPLETE",
                        "severity": "WARN",
                        "summary": f"Progress judge artifact missing fields: {', '.join(missing)}.",
                        "evidence_paths": [_relpath(judge_path)],
                        "recommended_actions": ["ACTION_RESTART_SERVICES_SIM_ONLY"],
                    }
                )
            if latest_run_complete:
                try:
                    judge_mtime = judge_path.stat().st_mtime
                    run_mtime = latest_run_complete.stat().st_mtime
                except OSError:
                    judge_mtime = None
                    run_mtime = None
                if judge_mtime is not None and run_mtime is not None and judge_mtime + 60 < run_mtime:
                    issues.append(
                        {
                            "code": "JUDGE_ARTIFACT_STALE",
                            "severity": "WARN",
                            "summary": "Progress judge artifact appears stale versus latest run_complete.json.",
                            "evidence_paths": sorted({_relpath(judge_path), _relpath(latest_run_complete)}),
                            "recommended_actions": ["ACTION_RESTART_SERVICES_SIM_ONLY"],
                        }
                    )

    heartbeat_issue, heartbeat_paths, heartbeat_detail = _service_heartbeat_issue(now)
    if heartbeat_issue:
        issues.append(
            {
                "code": "SERVICE_HEARTBEAT_STALE",
                "severity": "WARN",
                "summary": f"Training service heartbeat stale or missing ({heartbeat_detail}).",
                "evidence_paths": heartbeat_paths,
                "recommended_actions": ["ACTION_RESTART_SERVICES_SIM_ONLY"],
            }
        )

    doctor_payload = _load_doctor_report()
    doctor_summary: dict[str, Any] = {"status": "MISSING", "ts_utc": None, "issues_count": 0}
    if doctor_payload:
        doctor_issues_raw = doctor_payload.get("issues", [])
        doctor_issues: list[dict[str, Any]] = []
        if isinstance(doctor_issues_raw, list):
            doctor_issues = [
                _doctor_issue_to_action_center(entry)
                for entry in doctor_issues_raw
                if isinstance(entry, dict)
            ]
        issues.extend(doctor_issues)
        doctor_summary = {
            "status": "OK" if not doctor_issues else "ISSUE",
            "ts_utc": doctor_payload.get("ts_utc"),
            "issues_count": len(doctor_issues),
        }
    else:
        issues.append(
            {
                "code": "DOCTOR_REPORT_MISSING",
                "severity": "WARN",
                "summary": "Doctor report missing; run Doctor to refresh diagnostics.",
                "evidence_paths": [_relpath(DOCTOR_REPORT_PATH)],
                "recommended_actions": ["GEN_DOCTOR_REPORT"],
            }
        )

    if os.environ.get("CI_FORCE_FAIL") == "1":
        issues.append(
            {
                "code": "CI_FORCE_FAIL",
                "severity": "INFO",
                "summary": "CI_FORCE_FAIL enabled; failure is expected for evidence-pack validation.",
                "evidence_paths": ["artifacts/gates.log"],
                "recommended_actions": [],
            }
        )

    action_evidence: dict[str, set[str]] = {action_id: set() for action_id in ACTION_DEFINITIONS}
    for issue in issues:
        for action_id in issue.get("recommended_actions", []):
            evidence = issue.get("evidence_paths", [])
            action_evidence.setdefault(action_id, set()).update(str(path) for path in evidence)

    return {
        "ts_utc": now.isoformat(),
        "repo_ref": _repo_ref(),
        "environment_notes": environment_notes,
        "detected_issues": issues,
        "recommended_actions": _build_recommended_actions(action_evidence),
        "action_rows": _build_action_rows(issues, now, action_evidence),
        "doctor_summary": doctor_summary,
    }


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Action Center report generator (SIM-only, read-only)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path for action_center_report.json")
    parser.add_argument("--execute", choices=sorted(ACTION_DEFINITIONS.keys()), help="Execute a safe action")
    parser.add_argument("--confirm", default="", help="Typed confirmation token")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_path = Path(args.output)
    try:
        if args.execute:
            try:
                _ensure_not_ci()
            except RuntimeError as exc:
                report = build_report()
                write_report(report, output_path)
                print(str(exc))
                return 2
            expected = CONFIRM_TOKENS[args.execute]
            if not confirm_token_is_valid(args.confirm, expected):
                report = build_report()
                write_report(report, output_path)
                print("Confirmation token rejected.")
                return 3
            result = _execute_action(args.execute)
            report = build_report()
            write_report(report, output_path)
            if result.success:
                return 0
            print(result.message)
            return 1

        report = build_report()
        write_report(report, output_path)
        return 0
    except Exception as exc:
        fallback = {
            "ts_utc": _iso_now(),
            "repo_ref": _repo_ref(),
            "environment_notes": [
                {"code": "report_error", "detail": f"Action Center report failed: {exc}"}
            ],
            "detected_issues": [],
            "recommended_actions": _build_recommended_actions(),
            "action_rows": _build_action_rows([], _now()),
        }
        write_report(fallback, output_path)
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
