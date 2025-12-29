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

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
RUNS_DIR = LOGS_DIR / "train_runs"
LATEST_DIR = RUNS_DIR / "_latest"
PROGRESS_INDEX_PATH = RUNS_DIR / "progress_index.json"
STATE_PATH = LOGS_DIR / "train_service" / "state.json"
DEFAULT_OUTPUT = LOGS_DIR / "action_center_report.json"
SUPERVISOR_SCRIPT = ROOT / "tools" / "supervisor.py"
PROGRESS_INDEX_SCRIPT = ROOT / "tools" / "progress_index.py"

LATEST_POINTERS = [
    "candidates_latest.json",
    "tournament_latest.json",
    "promotion_decision_latest.json",
    "policy_history_latest.json",
    "progress_judge_latest.json",
]

CONFIRM_TOKENS = {
    "ACTION_CLEAR_KILL_SWITCH": "CLEAR",
    "ACTION_REBUILD_PROGRESS_INDEX": "REBUILD",
    "ACTION_RESTART_SERVICES_SIM_ONLY": "RESTART",
}

ACTION_DEFINITIONS = {
    "ACTION_CLEAR_KILL_SWITCH": {
        "title": "Clear kill switch (SIM-only)",
        "confirmation_token": CONFIRM_TOKENS["ACTION_CLEAR_KILL_SWITCH"],
        "safety_notes": "SIM-only. Removes local kill switch files and does not place trades.",
        "effect_summary": "Clears kill switch files via supervisor clear-kill-switch.",
    },
    "ACTION_REBUILD_PROGRESS_INDEX": {
        "title": "Rebuild progress index",
        "confirmation_token": CONFIRM_TOKENS["ACTION_REBUILD_PROGRESS_INDEX"],
        "safety_notes": "SIM-only. Regenerates Logs/train_runs/progress_index.json from local files.",
        "effect_summary": "Runs tools/progress_index.py to refresh the progress index.",
    },
    "ACTION_RESTART_SERVICES_SIM_ONLY": {
        "title": "Restart SIM services",
        "confirmation_token": CONFIRM_TOKENS["ACTION_RESTART_SERVICES_SIM_ONLY"],
        "safety_notes": "SIM-only. Restarts local supervisor-managed services; no broker access.",
        "effect_summary": "Stops and starts supervisor services (quotes/alerts).",
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


def _execute_clear_kill_switch() -> ActionExecutionResult:
    proc = _run_command([sys.executable, str(SUPERVISOR_SCRIPT), "clear-kill-switch"])
    details = {
        "command": proc.args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    if proc.returncode == 0:
        write_event(
            "KILL_SWITCH_CLEARED",
            "Action Center cleared kill switch",
            action_id="ACTION_CLEAR_KILL_SWITCH",
        )
        return ActionExecutionResult("ACTION_CLEAR_KILL_SWITCH", True, "kill switch cleared", details)
    write_event(
        "KILL_SWITCH_CLEAR_FAILED",
        "Action Center failed to clear kill switch",
        severity="ERROR",
        action_id="ACTION_CLEAR_KILL_SWITCH",
    )
    return ActionExecutionResult("ACTION_CLEAR_KILL_SWITCH", False, "kill switch clear failed", details)


def _execute_rebuild_progress_index() -> ActionExecutionResult:
    proc = _run_command([sys.executable, str(PROGRESS_INDEX_SCRIPT)])
    details = {
        "command": proc.args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    if proc.returncode == 0:
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
            "command": stop_proc.args,
            "returncode": stop_proc.returncode,
            "stdout": stop_proc.stdout,
            "stderr": stop_proc.stderr,
        },
        "start": {
            "command": start_proc.args if start_proc else [],
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


def _execute_action(action_id: str) -> ActionExecutionResult:
    if action_id == "ACTION_CLEAR_KILL_SWITCH":
        return _execute_clear_kill_switch()
    if action_id == "ACTION_REBUILD_PROGRESS_INDEX":
        return _execute_rebuild_progress_index()
    if action_id == "ACTION_RESTART_SERVICES_SIM_ONLY":
        return _execute_restart_services()
    raise ValueError(f"Unknown action_id: {action_id}")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


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
        recommended_actions.append(
            {
                "action_id": action_id,
                "title": action["title"],
                "requires_typed_confirmation": True,
                "confirmation_token": action["confirmation_token"],
                "safety_notes": action["safety_notes"],
                "effect_summary": action["effect_summary"],
                "related_evidence_paths": sorted(action_evidence.get(action_id, set())),
            }
        )
    return recommended_actions


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
                "recommended_actions": ["ACTION_CLEAR_KILL_SWITCH"],
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
        }
        write_report(fallback, output_path)
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
