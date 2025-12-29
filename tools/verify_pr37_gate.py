from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARTIFACTS_DIR = Path("artifacts")
COMPILE_LOG = ARTIFACTS_DIR / "pr37_ui_compile.log"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    combined = "\n".join(block for block in [result.stdout, result.stderr] if block)
    return result.returncode, combined.strip()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _copy_artifact(source: Path, target: Path) -> None:
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    compile_cmd = [sys.executable, "-m", "py_compile", "tools/ui_app.py"]
    rc, output = _run(compile_cmd)
    _write_text(COMPILE_LOG, output)
    if rc != 0:
        errors.append("ui_app_compile_failed")

    doctor_cmd = [sys.executable, "-m", "tools.doctor_report", "--output", str(ARTIFACTS_DIR / "doctor_report.json")]
    rc, output = _run(doctor_cmd)
    if rc != 0:
        errors.append(f"doctor_report_failed:{output}")
    doctor_payload = _read_json(ARTIFACTS_DIR / "doctor_report.json")
    git_status = doctor_payload.get("git_status")
    if not isinstance(git_status, dict) or "clean" not in git_status:
        errors.append("doctor_report_missing_git_status")
    if "git_dirty_files" not in doctor_payload:
        errors.append("doctor_report_missing_git_dirty_files")

    action_cmd = [
        sys.executable,
        "-m",
        "tools.action_center_report",
        "--output",
        str(ARTIFACTS_DIR / "action_center_report.json"),
    ]
    rc, output = _run(action_cmd)
    if rc != 0:
        errors.append(f"action_center_report_failed:{output}")
    action_payload = _read_json(ARTIFACTS_DIR / "action_center_report.json")
    recommended = action_payload.get("recommended_actions", [])
    if not any(
        isinstance(entry, dict) and entry.get("action_id") == "FIX_GIT_RED_SAFE" for entry in recommended
    ):
        errors.append("action_center_missing_fix_git_red_action")

    tmp_root = ARTIFACTS_DIR / "pr37_tmp_repo"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    rc, output = _run(["git", "clone", "--local", ".", str(tmp_root)])
    if rc != 0:
        errors.append(f"git_clone_failed:{output}")
    else:
        runtime_dir = tmp_root / "Logs" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "pr37_gate.tmp").write_text("probe", encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(tmp_root)
        apply_cmd = [
            sys.executable,
            "-m",
            "tools.action_center_apply",
            "--action-id",
            "FIX_GIT_RED_SAFE",
            "--confirm",
            "GITSAFE",
            "--dry-run",
        ]
        rc, output = _run(apply_cmd, cwd=tmp_root, env=env)
        if rc != 0:
            errors.append(f"git_hygiene_dry_run_failed:{output}")

        plan_path = tmp_root / "artifacts" / "git_hygiene_fix_plan.json"
        result_path = tmp_root / "artifacts" / "git_hygiene_fix_result.json"
        if not plan_path.exists():
            errors.append("git_hygiene_plan_missing")
        if not result_path.exists():
            errors.append("git_hygiene_result_missing")

        _copy_artifact(plan_path, ARTIFACTS_DIR / "git_hygiene_fix_plan.json")
        _copy_artifact(result_path, ARTIFACTS_DIR / "git_hygiene_fix_result.json")

    payload = {
        "status": "PASS" if not errors else "FAIL",
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "errors": errors,
    }
    _write_json(ARTIFACTS_DIR / "pr37_gate_result.json", payload)

    if errors:
        print("verify_pr37_gate FAIL")
        for err in errors:
            print(f" - {err}")
        return 1

    print("verify_pr37_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
