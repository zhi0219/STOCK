from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.doctor_report import build_report, write_report
from tools.pr28_training_loop import PR28Config, RUNS_ROOT, run_pr28_flow
from tools.promotion_gate_v2 import GateConfig, evaluate_promotion_gate
from tools.repo_hygiene import scan_repo
from tools.write_xp_snapshot import write_xp_snapshot

ARTIFACTS_DIR = Path("artifacts")
WALK_FORWARD_RESULT = ARTIFACTS_DIR / "walk_forward_result.json"
WALK_FORWARD_WINDOWS = ARTIFACTS_DIR / "walk_forward_windows.jsonl"
NO_LOOKAHEAD_RESULT = ARTIFACTS_DIR / "no_lookahead_audit.json"
XP_SNAPSHOT = ARTIFACTS_DIR / "xp_snapshot.json"
ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\")


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _contains_absolute_path(text: str) -> bool:
    if not text:
        return False
    if text.startswith("/"):
        return True
    if ABS_PATH_PATTERN.search(text):
        return True
    if re.match(r"^[A-Za-z]:", text):
        return True
    return False


def _assert_repo_relative(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, value in payload.items():
        if isinstance(value, str) and _contains_absolute_path(value):
            errors.append(f"absolute_path_detected:{key}")
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, str) and _contains_absolute_path(sub_value):
                    errors.append(f"absolute_path_detected:{key}.{sub_key}")
    return errors


def _run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = "\n".join(block for block in [result.stdout, result.stderr] if block)
    return result.returncode, combined.strip()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    runs_root = RUNS_ROOT / "_pr32_gate"
    config = PR28Config(
        runs_root=runs_root,
        seed=32,
        max_steps=80,
        candidate_count=2,
        min_steps=40,
        quotes_limit=120,
    )
    run_pr28_flow(config)

    doctor_report = build_report()
    write_report(doctor_report, ARTIFACTS_DIR / "doctor_report.json")

    hygiene_payload = scan_repo()
    _write_json(ARTIFACTS_DIR / "repo_hygiene.json", hygiene_payload)

    rc, output = _run(
        [
            sys.executable,
            "-m",
            "tools.walk_forward_eval",
            "--output-dir",
            str(ARTIFACTS_DIR),
            "--latest-dir",
            str(ARTIFACTS_DIR),
            "--window-count",
            "3",
            "--window-passes-required",
            "2",
        ]
    )
    if rc != 0:
        errors.append(f"walk_forward_eval_failed:{output}")

    rc, output = _run(
        [
            sys.executable,
            "-m",
            "tools.no_lookahead_audit",
            "--output-dir",
            str(ARTIFACTS_DIR),
            "--latest-dir",
            str(ARTIFACTS_DIR),
        ]
    )
    if rc != 0:
        errors.append(f"no_lookahead_audit_failed:{output}")

    if not WALK_FORWARD_RESULT.exists():
        errors.append("walk_forward_result_missing")
    if not WALK_FORWARD_WINDOWS.exists():
        errors.append("walk_forward_windows_missing")
    if not NO_LOOKAHEAD_RESULT.exists():
        errors.append("no_lookahead_audit_missing")
    if not (ARTIFACTS_DIR / "walk_forward_result_latest.json").exists():
        errors.append("walk_forward_result_latest_missing")
    if not (ARTIFACTS_DIR / "walk_forward_windows_latest.jsonl").exists():
        errors.append("walk_forward_windows_latest_missing")
    if not (ARTIFACTS_DIR / "no_lookahead_audit_latest.json").exists():
        errors.append("no_lookahead_audit_latest_missing")

    write_xp_snapshot(runs_root=runs_root, artifacts_output=XP_SNAPSHOT)
    if not XP_SNAPSHOT.exists():
        errors.append("xp_snapshot_missing")
    latest_snapshot = runs_root / "progress_xp" / "xp_snapshot_latest.json"
    if latest_snapshot.exists():
        (ARTIFACTS_DIR / "xp_snapshot_latest.json").write_text(
            latest_snapshot.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    walk_forward_payload = _safe_read_json(WALK_FORWARD_RESULT)
    no_lookahead_payload = _safe_read_json(NO_LOOKAHEAD_RESULT)
    xp_snapshot_payload = _safe_read_json(XP_SNAPSHOT)

    errors.extend(_assert_repo_relative(walk_forward_payload))
    errors.extend(_assert_repo_relative(no_lookahead_payload))
    if xp_snapshot_payload:
        for path in xp_snapshot_payload.get("source_artifacts", {}).values():
            if isinstance(path, str) and _contains_absolute_path(path):
                errors.append("absolute_path_detected:xp_snapshot_source_artifacts")

    candidate = {"candidate_id": "candidate_gate", "score": 10.0, "max_drawdown_pct": 1.0, "turnover": 1, "reject_rate": 0.0}
    baselines = [{"candidate_id": "baseline_do_nothing", "score": 5.0}]
    decision = evaluate_promotion_gate(
        candidate,
        baselines,
        "pr32_missing",
        GateConfig(require_walk_forward=True),
        stress_report={"status": "PASS", "baseline_pass": True, "stress_pass": True, "scenarios": [{"pass": True}]},
        walk_forward_result=None,
    )
    if decision.get("decision") != "REJECT":
        errors.append("promotion_gate_missing_walk_forward_not_rejected")
    if "walk_forward_constraints_failed" not in decision.get("reasons", []):
        errors.append("promotion_gate_missing_walk_forward_reason")

    if os.environ.get("PR32_FORCE_FAIL") == "1":
        errors.append("PR32_FORCE_FAIL")

    result_payload = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
    }
    _write_json(ARTIFACTS_DIR / "pr32_gate_result.json", result_payload)

    if errors:
        print("verify_pr32_gate FAIL")
        for err in errors:
            print(f" - {err}")
        return 1

    print("verify_pr32_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
