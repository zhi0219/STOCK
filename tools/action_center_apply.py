from __future__ import annotations

import argparse
import json
import os
import sys
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
DEFAULT_EVIDENCE_DIR = ROOT / "artifacts" / "action_center_apply"
DEFAULT_RESULT_PATH = ROOT / "artifacts" / "action_center_apply_result.json"
CONFIG_PATH = ROOT / "config.yaml"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import action_center_report
from tools import git_hygiene_fix
from tools.paths import to_repo_relative


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event_path(now: datetime) -> Path:
    return LOGS_DIR / f"events_{now:%Y-%m-%d}.jsonl"


def _write_event(event_type: str, message: str, severity: str = "INFO", **extra: Any) -> dict[str, Any]:
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
    payload["events_path"] = to_repo_relative(path)
    return payload


def _log_line(handle, message: str) -> None:
    ts = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} | {message}"
    print(line, flush=True)
    handle.write(line + "\n")
    handle.flush()


def _load_config_mode() -> str | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("mode")
    return str(value) if value is not None else None


def _sim_only_guard() -> tuple[bool, list[str]]:
    reasons: list[str] = []
    mode = _load_config_mode()
    if mode is None:
        reasons.append("config.yaml missing or unreadable")
    else:
        normalized = mode.strip().upper()
        if normalized not in {"READ_ONLY", "SIM", "SIM_ONLY"}:
            reasons.append(f"config mode={mode}")

    env_flags = {
        "LIVE_TRADING": {"1", "true", "yes"},
        "BROKER_LIVE": {"1", "true", "yes"},
    }
    for key, truthy in env_flags.items():
        if os.environ.get(key, "").strip().lower() in truthy:
            reasons.append(f"env {key}={os.environ.get(key)}")

    mode_env = os.environ.get("STOCK_MODE") or os.environ.get("TRADING_MODE") or os.environ.get("MODE")
    if mode_env:
        normalized = mode_env.strip().upper()
        if normalized not in {"READ_ONLY", "SIM", "SIM_ONLY"}:
            reasons.append(f"env mode={mode_env}")

    return not reasons, reasons


def _excerpt(text: str, limit: int = 2000) -> str:
    if not text:
        return ""
    text = _sanitize_text(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _sanitize_text(text: str) -> str:
    if not text:
        return text
    root_text = str(ROOT.resolve())
    redacted = text.replace(root_text, "<repo_root>")
    redacted = re.sub(r"[A-Za-z]:\\\\[^\s\"']+", "<win_path_redacted>", redacted)
    return redacted


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _list_actions() -> int:
    print("Safe actions (SIM-only, confirmation required):")
    for idx, action_id in enumerate(sorted(action_center_report.ACTION_DEFINITIONS.keys()), start=1):
        action = action_center_report.ACTION_DEFINITIONS[action_id]
        token = action.get("confirmation_token", "")
        print(f"{idx}. {action_id} - {action['title']} (token: {token})")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Action Center apply entrypoint (SIM-only, READ_ONLY)")
    parser.add_argument(
        "--action-id",
        choices=sorted(action_center_report.ACTION_DEFINITIONS.keys()),
        help="Safe action id to apply.",
    )
    parser.add_argument("--confirm", default="", help="Typed confirmation token for the selected action")
    parser.add_argument(
        "--ui-confirmed",
        action="store_true",
        help="UI-only confirmation (checkbox + press-and-hold).",
    )
    parser.add_argument("--dry-run", action="store_true", help="No mutations (evidence only)")
    parser.add_argument(
        "--evidence-dir",
        default=str(DEFAULT_EVIDENCE_DIR),
        help="Directory to write action_center_apply evidence files",
    )
    parser.add_argument(
        "--result-path",
        default=str(DEFAULT_RESULT_PATH),
        help="Path to write action_center_apply_result.json",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.action_id:
        return _list_actions()

    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log_path = evidence_dir / "action_center_apply.log"
    summary_path = evidence_dir / "action_center_apply_summary.json"
    result_path = Path(args.result_path)
    action_id = args.action_id
    expected_token = action_center_report.CONFIRM_TOKENS.get(action_id, "")
    ts_utc = _now().isoformat()
    action_definition = action_center_report.ACTION_DEFINITIONS.get(action_id, {})
    risk_level = str(action_definition.get("risk_level", "SAFE")).upper()
    linked_doctor_report = None
    doctor_path = ROOT / "artifacts" / "doctor_report.json"
    if doctor_path.exists():
        linked_doctor_report = to_repo_relative(doctor_path)

    summary: dict[str, Any] = {
        "schema_version": 1,
        "ts_utc": ts_utc,
        "action_id": action_id,
        "risk_level": risk_level,
        "ui_confirmed": bool(args.ui_confirmed),
        "dry_run": bool(args.dry_run),
        "status": "UNKNOWN",
        "overall_status": "UNKNOWN",
        "message": "",
        "evidence_dir": to_repo_relative(evidence_dir),
        "events_path": None,
        "action_definition": action_definition,
        "sim_guard": {"allowed": None, "reasons": []},
        "preconditions_checked": [],
        "changes_made": [],
        "linked_doctor_report": linked_doctor_report,
    }

    result_payload: dict[str, Any] = {
        "schema_version": 2,
        "marker": "ACTION_CENTER_APPLY_RESULT",
        "ts_utc": ts_utc,
        "action_id": action_id,
        "risk_level": risk_level,
        "ui_confirmed": bool(args.ui_confirmed),
        "dry_run": bool(args.dry_run),
        "overall_status": "UNKNOWN",
        "status": "UNKNOWN",
        "message": "",
        "evidence_dir": summary["evidence_dir"],
        "events_path": None,
        "preconditions_checked": [],
        "changes_made": [],
        "error_excerpt": "",
        "linked_doctor_report": linked_doctor_report,
        "action_definition": action_definition,
        "sim_guard": summary["sim_guard"],
        "stdout_excerpt": "",
        "stderr_excerpt": "",
    }

    plan_path = ROOT / "artifacts" / "action_center_apply_plan.json"

    def finalize(exit_code: int) -> int:
        _write_json(summary_path, summary)
        result_payload.update(
            {
                "status": summary.get("status"),
                "overall_status": summary.get("overall_status"),
                "message": summary.get("message"),
                "events_path": summary.get("events_path"),
                "sim_guard": summary.get("sim_guard"),
                "preconditions_checked": summary.get("preconditions_checked", []),
                "changes_made": summary.get("changes_made", []),
                "linked_doctor_report": summary.get("linked_doctor_report"),
            }
        )
        _write_json(result_path, result_payload)
        return exit_code

    with log_path.open("a", encoding="utf-8") as handle:
        _log_line(handle, f"ACTION_CENTER_APPLY_START action_id={action_id} dry_run={args.dry_run}")
        event = _write_event(
            "ACTION_CENTER_APPLY_ATTEMPT",
            "Action Center apply attempt recorded.",
            action_id=action_id,
            dry_run=bool(args.dry_run),
            evidence_dir=to_repo_relative(evidence_dir),
            marker="ACTION_CENTER_APPLY",
        )
        summary["events_path"] = event.get("events_path")
        result_payload["events_path"] = summary["events_path"]

        preconditions: list[dict[str, Any]] = []
        token_ok = action_center_report.confirm_token_is_valid(args.confirm, expected_token)
        preconditions.append(
            {"name": "confirmation_token", "status": "PASS" if token_ok else "FAIL", "detail": expected_token}
        )
        if risk_level in {"CAUTION", "DANGEROUS"}:
            ui_ok = bool(args.ui_confirmed)
            preconditions.append(
                {"name": "ui_confirmed", "status": "PASS" if ui_ok else "FAIL", "detail": risk_level}
            )
        else:
            preconditions.append({"name": "ui_confirmed", "status": "PASS", "detail": risk_level})

        allowed, reasons = _sim_only_guard()
        summary["sim_guard"] = {"allowed": allowed, "reasons": reasons}
        preconditions.append(
            {"name": "sim_only_guard", "status": "PASS" if allowed else "FAIL", "detail": "; ".join(reasons)}
        )

        if not args.dry_run:
            try:
                action_center_report._ensure_not_ci()
                preconditions.append({"name": "ci_guard", "status": "PASS", "detail": "not_ci"})
            except RuntimeError as exc:
                preconditions.append({"name": "ci_guard", "status": "FAIL", "detail": str(exc)})
        else:
            preconditions.append({"name": "ci_guard", "status": "PASS", "detail": "dry_run"})

        summary["preconditions_checked"] = preconditions
        plan_payload = {
            "ts_utc": ts_utc,
            "action_id": action_id,
            "risk_level": risk_level,
            "ui_confirmed": bool(args.ui_confirmed),
            "dry_run": bool(args.dry_run),
            "preconditions_checked": preconditions,
            "linked_doctor_report": linked_doctor_report,
        }
        _write_json(plan_path, plan_payload)

        if any(entry["status"] == "FAIL" for entry in preconditions):
            _log_line(handle, "ACTION_CENTER_APPLY_REFUSED preconditions failed.")
            summary["status"] = "REFUSED"
            summary["overall_status"] = "REFUSED"
            summary["message"] = "Preconditions failed; no changes applied."
            result_payload["error_excerpt"] = _excerpt(summary["message"])
            _write_event(
                "ACTION_CENTER_APPLY_REFUSED",
                "Action Center apply refused (preconditions failed).",
                severity="ERROR",
                action_id=action_id,
                dry_run=bool(args.dry_run),
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(4)

        if args.dry_run:
            if action_id == "FIX_GIT_RED_SAFE":
                plan = git_hygiene_fix.build_plan()
                git_hygiene_fix.write_plan(plan)
                result = git_hygiene_fix.apply_fix(plan, dry_run=True)
                git_hygiene_fix.write_result(result)
                summary["changes_made"] = [
                    to_repo_relative(git_hygiene_fix.PLAN_PATH),
                    to_repo_relative(git_hygiene_fix.RESULT_PATH),
                ]
                summary["message"] = "Dry run completed; git hygiene plan/result written."
            else:
                summary["message"] = "Dry run completed; no mutations executed."
            _log_line(handle, "ACTION_CENTER_APPLY_DRY_RUN no mutations executed.")
            summary["status"] = "DRY_RUN"
            summary["overall_status"] = "PASS"
            _write_event(
                "ACTION_CENTER_APPLY_DRY_RUN",
                "Action Center apply dry-run completed.",
                action_id=action_id,
                dry_run=True,
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(0)

        try:
            result = action_center_report._execute_action(action_id)
        except Exception as exc:
            _log_line(handle, f"ACTION_CENTER_APPLY_FAILED unexpected error: {exc}")
            summary["status"] = "FAILED"
            summary["overall_status"] = "FAIL"
            summary["message"] = f"Action execution failed: {exc}"
            result_payload["error_excerpt"] = _excerpt(str(exc))
            _write_event(
                "ACTION_CENTER_APPLY_FAILED",
                "Action Center apply failed.",
                severity="ERROR",
                action_id=action_id,
                dry_run=False,
                error=str(exc),
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(1)

        summary["action_result"] = asdict(result)
        changes = result.details.get("changes_made", []) if isinstance(result.details, dict) else []
        summary["changes_made"] = list(changes) if isinstance(changes, list) else []
        if action_id == "ENABLE_OVERTRADING_GUARDRAILS_SAFE":
            evidence_payload = {
                "ts_utc": ts_utc,
                "action_id": action_id,
                "runtime_config_path": result.details.get("output_path") if isinstance(result.details, dict) else None,
                "status": "APPLIED" if result.success else "FAILED",
            }
            evidence_path = evidence_dir / "overtrading_guardrails_evidence.json"
            _write_json(evidence_path, evidence_payload)
            summary["changes_made"].append(to_repo_relative(evidence_path))
        if "stdout" in result.details:
            result_payload["stdout_excerpt"] = _excerpt(str(result.details.get("stdout", "")))
            result_payload["stderr_excerpt"] = _excerpt(str(result.details.get("stderr", "")))
        else:
            combined_stdout = "\n".join(
                str(block.get("stdout", "")) for block in result.details.values() if isinstance(block, dict)
            )
            combined_stderr = "\n".join(
                str(block.get("stderr", "")) for block in result.details.values() if isinstance(block, dict)
            )
            result_payload["stdout_excerpt"] = _excerpt(combined_stdout)
            result_payload["stderr_excerpt"] = _excerpt(combined_stderr)
        result_payload["action_result"] = summary.get("action_result")
        if result.success:
            _log_line(handle, "ACTION_CENTER_APPLY_SUCCESS action executed successfully.")
            summary["status"] = "SUCCESS"
            summary["overall_status"] = "PASS"
            summary["message"] = result.message
            _write_event(
                "ACTION_CENTER_APPLY_SUCCESS",
                "Action Center apply succeeded.",
                action_id=action_id,
                dry_run=False,
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(0)

        if isinstance(result.details, dict) and result.details.get("refused"):
            summary["status"] = "REFUSED"
            summary["overall_status"] = "REFUSED"
        else:
            summary["status"] = "FAILED"
            summary["overall_status"] = "FAIL"
        summary["message"] = result.message
        result_payload["error_excerpt"] = _excerpt(result.message)
        _log_line(handle, f"ACTION_CENTER_APPLY_FAILED {result.message}")
        _write_event(
            "ACTION_CENTER_APPLY_FAILED",
            "Action Center apply failed.",
            severity="ERROR",
            action_id=action_id,
            dry_run=False,
            marker="ACTION_CENTER_APPLY",
        )
        return finalize(1)


if __name__ == "__main__":
    raise SystemExit(main())
