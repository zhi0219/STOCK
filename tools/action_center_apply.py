from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
DEFAULT_EVIDENCE_DIR = ROOT / "artifacts" / "action_center_apply"

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


def _list_actions() -> int:
    print("Safe actions (SIM-only, confirmation required):")
    for idx, action_id in enumerate(sorted(action_center_report.ACTION_DEFINITIONS.keys()), start=1):
        action = action_center_report.ACTION_DEFINITIONS[action_id]
        token = f"APPLY:{action_id}"
        print(f"{idx}. {action_id} - {action['title']} (token: {token})")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Action Center apply entrypoint (SIM-only, READ_ONLY)")
    parser.add_argument(
        "--action-id",
        choices=sorted(action_center_report.ACTION_DEFINITIONS.keys()),
        help="Safe action id to apply.",
    )
    parser.add_argument("--confirm", default="", help="Typed confirmation token (APPLY:<action_id>)")
    parser.add_argument("--dry-run", action="store_true", help="No mutations (evidence only)")
    parser.add_argument(
        "--evidence-dir",
        default=str(DEFAULT_EVIDENCE_DIR),
        help="Directory to write action_center_apply evidence files",
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
    action_id = args.action_id
    expected_token = f"APPLY:{action_id}"

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
    }

    with log_path.open("a", encoding="utf-8") as handle:
        _log_line(handle, f"ACTION_CENTER_APPLY_START action_id={action_id} dry_run={args.dry_run}")
        event = _write_event(
            "ACTION_CENTER_APPLY_ATTEMPT",
            "Action Center apply attempt recorded.",
            action_id=action_id,
            dry_run=bool(args.dry_run),
            evidence_dir=evidence_dir.as_posix(),
        )
        summary["events_path"] = event.get("events_path")

        if args.confirm.strip() != expected_token:
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
            )
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            return 3

        if args.dry_run:
            _log_line(handle, "ACTION_CENTER_APPLY_DRY_RUN no mutations executed.")
            summary["status"] = "DRY_RUN"
            summary["message"] = "Dry run completed; no mutations executed."
            _write_event(
                "ACTION_CENTER_APPLY_DRY_RUN",
                "Action Center apply dry-run completed.",
                action_id=action_id,
                dry_run=True,
            )
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            return 0

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
            )
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            return 2

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
            )
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            return 1

        summary["action_result"] = asdict(result)
        if result.success:
            _log_line(handle, "ACTION_CENTER_APPLY_SUCCESS action executed successfully.")
            summary["status"] = "SUCCESS"
            summary["message"] = result.message
            _write_event(
                "ACTION_CENTER_APPLY_SUCCESS",
                "Action Center apply succeeded.",
                action_id=action_id,
                dry_run=False,
            )
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            return 0

        _log_line(handle, f"ACTION_CENTER_APPLY_FAILED {result.message}")
        summary["status"] = "FAILED"
        summary["message"] = result.message
        _write_event(
            "ACTION_CENTER_APPLY_FAILED",
            "Action Center apply failed.",
            severity="ERROR",
            action_id=action_id,
            dry_run=False,
        )
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
