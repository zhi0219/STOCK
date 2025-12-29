from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts"


class GateError(Exception):
    pass


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _check_imports() -> None:
    try:
        from tools import action_center_report  # noqa: F401
        from tools import action_center_apply  # noqa: F401
        from tools import doctor_report  # noqa: F401
    except Exception as exc:  # pragma: no cover - static gate
        raise GateError(f"failed to import doctor/action center modules: {exc}") from exc


def _run_doctor_report(extra_env: dict[str, str] | None = None) -> Path:
    output_path = ARTIFACTS_DIR / "doctor_report.json"
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, "-m", "tools.doctor_report", "--output", str(output_path)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if proc.returncode != 0:
        raise GateError(f"doctor_report failed: {proc.stderr or proc.stdout}")
    if not output_path.exists():
        raise GateError("doctor_report did not write artifacts/doctor_report.json")
    return output_path


def _run_action_center_report(extra_env: dict[str, str] | None = None) -> dict[str, object]:
    output_path = ARTIFACTS_DIR / "action_center_report.json"
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, "-m", "tools.action_center_report", "--output", str(output_path)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if proc.returncode != 0:
        raise GateError(f"action_center_report failed: {proc.stderr or proc.stdout}")
    if not output_path.exists():
        raise GateError("action_center_report did not write artifacts/action_center_report.json")
    return _load_json(output_path)


def _check_injected_actions(report: dict[str, object]) -> None:
    required_action_ids = {
        "GEN_DOCTOR_REPORT",
        "REPO_HYGIENE_FIX_SAFE",
        "CLEAR_KILL_SWITCH",
        "CLEAR_STALE_TEMP",
        "ENSURE_RUNTIME_DIRS",
        "DIAG_RUNTIME_WRITE",
        "ABS_PATH_SANITIZE_HINT",
    }
    detected = report.get("detected_issues", [])
    action_ids: set[str] = set()
    if isinstance(detected, list):
        for entry in detected:
            if not isinstance(entry, dict):
                continue
            for action_id in entry.get("recommended_actions", []):
                action_ids.add(str(action_id))
    missing = required_action_ids.difference(action_ids)
    if missing:
        raise GateError(f"action_center_report missing injected action ids: {sorted(missing)}")


def _run_apply_dry_run() -> None:
    from tools.action_center_report import CONFIRM_TOKENS

    action_id = "GEN_DOCTOR_REPORT"
    confirm = CONFIRM_TOKENS[action_id]
    cmd = [
        sys.executable,
        "-m",
        "tools.action_center_apply",
        "--action-id",
        action_id,
        "--confirm",
        confirm,
        "--dry-run",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise GateError(f"action_center_apply dry-run failed: {proc.stderr or proc.stdout}")
    result_path = ARTIFACTS_DIR / "action_center_apply_result.json"
    if not result_path.exists():
        raise GateError("action_center_apply_result.json missing after dry-run")
    payload = _load_json(result_path)
    if payload.get("status") != "DRY_RUN":
        raise GateError("action_center_apply_result status is not DRY_RUN")
    if payload.get("overall_status") not in {"PASS", "UNKNOWN"}:
        raise GateError("action_center_apply_result overall_status unexpected")
    plan_path = ARTIFACTS_DIR / "action_center_apply_plan.json"
    if not plan_path.exists():
        raise GateError("action_center_apply_plan.json missing after dry-run")


def _check_ci_upload_contract() -> None:
    workflow = ROOT / ".github" / "workflows" / "ci_gates.yml"
    if not workflow.exists():
        return
    content = workflow.read_text(encoding="utf-8")
    if "Upload evidence pack" in content and "if: always()" not in content:
        raise GateError("ci_gates.yml evidence pack upload is not always()")


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PR30 gate: Doctor + Action Center hardening.")
    parser.add_argument("--force-fail", action="store_true", help="Force a failure for demo.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _check_imports()
        _run_doctor_report()
        _run_doctor_report({"PR30_INJECT_ISSUES": "1"})
        report = _run_action_center_report({"PR30_INJECT_ISSUES": "1"})
        _check_injected_actions(report)
        _run_apply_dry_run()
        _check_ci_upload_contract()
        if args.force_fail or os.environ.get("PR30_FORCE_FAIL") == "1":
            raise GateError("PR30 forced failure (demo flag set).")
    except GateError as exc:
        print(f"PR30 gate failed: {exc}")
        return 1
    print("PR30 gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
