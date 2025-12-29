from __future__ import annotations

import argparse
import json
import os
import sys
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
    payload["events_path"] = path.as_posix()
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
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


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

    summary: dict[str, Any] = {
        "schema_version": 1,
        "ts_utc": _now().isoformat(),
        "action_id": action_id,
        "dry_run": bool(args.dry_run),
        "status": "UNKNOWN",
        "message": "",
        "evidence_dir": evidence_dir.as_posix(),
        "events_path": None,
        "action_definition": action_center_report.ACTION_DEFINITIONS.get(action_id, {}),
        "sim_guard": {"allowed": None, "reasons": []},
    }

    result_payload: dict[str, Any] = {
        "schema_version": 1,
        "marker": "ACTION_CENTER_APPLY_RESULT",
        "ts_utc": summary["ts_utc"],
        "action_id": action_id,
        "dry_run": bool(args.dry_run),
        "status": "UNKNOWN",
        "message": "",
        "evidence_dir": summary["evidence_dir"],
        "events_path": None,
        "action_definition": summary["action_definition"],
        "sim_guard": summary["sim_guard"],
        "stdout_excerpt": "",
        "stderr_excerpt": "",
    }

    def finalize(exit_code: int) -> int:
        _write_json(summary_path, summary)
        result_payload.update(
            {
                "status": summary.get("status"),
                "message": summary.get("message"),
                "events_path": summary.get("events_path"),
                "sim_guard": summary.get("sim_guard"),
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
            evidence_dir=evidence_dir.as_posix(),
            marker="ACTION_CENTER_APPLY",
        )
        summary["events_path"] = event.get("events_path")
        result_payload["events_path"] = summary["events_path"]

        if not action_center_report.confirm_token_is_valid(args.confirm, expected_token):
            _log_line(handle, "ACTION_CENTER_APPLY_REJECTED invalid confirmation token.")
            summary["status"] = "REJECTED"
            summary["message"] = "Confirmation token rejected."
            _write_event(
                "ACTION_CENTER_APPLY_REJECTED",
                "Action Center apply rejected (invalid confirmation).",
                severity="ERROR",
                action_id=action_id,
                dry_run=bool(args.dry_run),
                expected_token=expected_token,
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(3)

        allowed, reasons = _sim_only_guard()
        summary["sim_guard"] = {"allowed": allowed, "reasons": reasons}
        result_payload["sim_guard"] = summary["sim_guard"]
        if not allowed:
            reason_text = "; ".join(reasons) if reasons else "SIM-only guard blocked apply."
            _log_line(handle, f"ACTION_CENTER_APPLY_REJECTED non-sim: {reason_text}")
            summary["status"] = "REJECTED"
            summary["message"] = "SIM-only guard rejected apply."
            _write_event(
                "ACTION_CENTER_APPLY_REJECTED_NON_SIM",
                "Action Center apply rejected (non-sim mode).",
                severity="ERROR",
                action_id=action_id,
                dry_run=bool(args.dry_run),
                reasons=reasons,
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(4)

        if args.dry_run:
            _log_line(handle, "ACTION_CENTER_APPLY_DRY_RUN no mutations executed.")
            summary["status"] = "DRY_RUN"
            summary["message"] = "Dry run completed; no mutations executed."
            _write_event(
                "ACTION_CENTER_APPLY_DRY_RUN",
                "Action Center apply dry-run completed.",
                action_id=action_id,
                dry_run=True,
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(0)

        try:
            action_center_report._ensure_not_ci()
        except RuntimeError as exc:
            _log_line(handle, f"ACTION_CENTER_APPLY_BLOCKED {exc}")
            summary["status"] = "BLOCKED"
            summary["message"] = str(exc)
            _write_event(
                "ACTION_CENTER_APPLY_BLOCKED",
                "Action Center apply blocked in CI.",
                severity="ERROR",
                action_id=action_id,
                dry_run=False,
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(2)

        try:
            result = action_center_report._execute_action(action_id)
        except Exception as exc:
            _log_line(handle, f"ACTION_CENTER_APPLY_FAILED unexpected error: {exc}")
            summary["status"] = "FAILED"
            summary["message"] = f"Action execution failed: {exc}"
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
            summary["message"] = result.message
            _write_event(
                "ACTION_CENTER_APPLY_SUCCESS",
                "Action Center apply succeeded.",
                action_id=action_id,
                dry_run=False,
                marker="ACTION_CENTER_APPLY",
            )
            return finalize(0)

        _log_line(handle, f"ACTION_CENTER_APPLY_FAILED {result.message}")
        summary["status"] = "FAILED"
        summary["message"] = result.message
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
