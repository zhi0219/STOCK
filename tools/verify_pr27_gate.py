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


def _check_ui_wiring() -> None:
    ui_path = ROOT / "tools" / "ui_app.py"
    if not ui_path.exists():
        raise GateError("ui_app.py missing")
    text = ui_path.read_text(encoding="utf-8")
    required_markers = [
        "Action Center",
        "Apply Selected Action",
        "ACTION_CENTER_STATUS",
        "Doctor",
    ]
    for marker in required_markers:
        if marker not in text:
            raise GateError(f"ui_app.py missing UI marker: {marker}")


def _check_imports() -> None:
    try:
        from tools import action_center_report  # noqa: F401
        from tools import action_center_apply  # noqa: F401
    except Exception as exc:  # pragma: no cover - static gate
        raise GateError(f"failed to import action_center modules: {exc}") from exc


def _run_report() -> None:
    output_path = ARTIFACTS_DIR / "action_center_report.json"
    cmd = [sys.executable, "-m", "tools.action_center_report", "--output", str(output_path)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise GateError(f"action_center_report failed: {proc.stderr or proc.stdout}")
    if not output_path.exists():
        raise GateError("action_center_report did not write artifacts/action_center_report.json")


def _run_apply_dry_run() -> None:
    from tools.action_center_report import CONFIRM_TOKENS

    action_id = "ACTION_REBUILD_PROGRESS_INDEX"
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


def _check_ci_artifacts() -> None:
    if not (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")):
        return
    required = [
        ARTIFACTS_DIR / "action_center_report.json",
        ARTIFACTS_DIR / "action_center_apply_result.json",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        missing_list = ", ".join(str(path) for path in missing)
        raise GateError(f"missing CI artifacts: {missing_list}")


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PR27 gate: Action Center UI/apply/report wiring.")
    parser.add_argument("--force-fail", action="store_true", help="Force a failure for demo.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _check_imports()
        _check_ui_wiring()
        _run_report()
        _run_apply_dry_run()
        _check_ci_artifacts()
        if args.force_fail or os.environ.get("PR27_FORCE_FAIL") == "1":
            raise GateError("PR27 forced failure (demo flag set).")
    except GateError as exc:
        print(f"PR27 gate failed: {exc}")
        return 1
    print("PR27 gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
