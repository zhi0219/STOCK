from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent


class GateError(Exception):
    pass


def _load_action_center_defs() -> tuple[dict[str, dict[str, Any]], dict[str, str], Any]:
    from tools import action_center_report

    return (
        action_center_report.ACTION_DEFINITIONS,
        action_center_report.CONFIRM_TOKENS,
        action_center_report.confirm_token_is_valid,
    )


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _validate_report_schema(report: dict[str, Any]) -> None:
    required_top = {"ts_utc", "repo_ref", "environment_notes", "detected_issues", "recommended_actions"}
    missing = required_top.difference(report.keys())
    if missing:
        raise GateError(f"report missing keys: {sorted(missing)}")
    repo_ref = report.get("repo_ref")
    if not isinstance(repo_ref, dict) or "git_commit_short" not in repo_ref:
        raise GateError("repo_ref missing git_commit_short")
    if not isinstance(report.get("environment_notes"), list):
        raise GateError("environment_notes must be a list")
    if not isinstance(report.get("detected_issues"), list):
        raise GateError("detected_issues must be a list")
    if not isinstance(report.get("recommended_actions"), list):
        raise GateError("recommended_actions must be a list")

    for issue in report.get("detected_issues", []):
        if not isinstance(issue, dict):
            raise GateError("detected_issues entries must be dicts")
        for key in ("code", "severity", "summary", "evidence_paths", "recommended_actions"):
            if key not in issue:
                raise GateError(f"detected issue missing {key}")

    for action in report.get("recommended_actions", []):
        if not isinstance(action, dict):
            raise GateError("recommended_actions entries must be dicts")
        for key in (
            "action_id",
            "title",
            "requires_typed_confirmation",
            "confirmation_token",
            "safety_notes",
            "effect_summary",
            "related_evidence_paths",
        ):
            if key not in action:
                raise GateError(f"recommended action missing {key}")


def _check_report_if_present() -> None:
    candidates = [
        ROOT / "Logs" / "action_center_report.json",
        ROOT / "artifacts" / "action_center_report.json",
    ]
    for path in candidates:
        payload = _load_json(path)
        if payload is None:
            continue
        _validate_report_schema(payload)


def _check_confirmation_gates() -> None:
    action_definitions, confirm_tokens, confirm_token_is_valid = _load_action_center_defs()
    expected_ids = {
        "ACTION_CLEAR_KILL_SWITCH",
        "ACTION_REBUILD_PROGRESS_INDEX",
        "ACTION_RESTART_SERVICES_SIM_ONLY",
    }
    if set(action_definitions.keys()) != expected_ids:
        raise GateError("action definitions missing required action ids")
    for action_id, token in confirm_tokens.items():
        if action_id not in action_definitions:
            raise GateError(f"missing action definition for {action_id}")
        if token != action_definitions[action_id]["confirmation_token"]:
            raise GateError(f"confirmation token mismatch for {action_id}")
        if confirm_token_is_valid("", token):
            raise GateError(f"empty token accepted for {action_id}")
        if confirm_token_is_valid("WRONG", token):
            raise GateError(f"wrong token accepted for {action_id}")
        if not confirm_token_is_valid(token, token):
            raise GateError(f"expected token rejected for {action_id}")


def _check_ci_artifact_listing() -> None:
    if not (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")):
        return
    script = (ROOT / "scripts" / "ci_gates.sh").read_text(encoding="utf-8")
    if "action_center_report.json" not in script:
        raise GateError("ci_gates.sh does not reference action_center_report.json")


def _check_imports_resolve() -> None:
    try:
        from tools import action_center_report  # noqa: F401
    except Exception as exc:  # pragma: no cover - static gate
        raise GateError(f"failed to import action_center_report: {exc}") from exc


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PR23 gate: validate Action Center contract and CI evidence bindings."
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Run a minimal self-check (no file IO) and exit.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main() -> int:
    args = _parse_args()
    try:
        _check_imports_resolve()
        if args.self_check:
            print("PR23 gate self-check ok")
            return 0
        _check_report_if_present()
        _check_confirmation_gates()
        _check_ci_artifact_listing()
    except GateError as exc:
        print(f"PR23 gate failed: {exc}")
        return 1
    print("PR23 gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
