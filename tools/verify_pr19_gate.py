from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ui_parsers import (
    load_engine_status,
    load_policy_history_latest,
    load_progress_judge_latest,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_py_compile(targets: List[Path]) -> tuple[bool, str]:
    args = [str(path) for path in targets]
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return False, output.strip()
    return True, "ok"


def _run_repo_hygiene() -> tuple[bool, str]:
    script = TOOLS_DIR / "verify_repo_hygiene.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    return result.returncode == 0, output.strip()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_required(payload: dict, required: List[str]) -> List[str]:
    return [key for key in required if key not in payload]


def _synthetic_artifacts() -> tuple[bool, str]:
    required_fields = ["schema_version", "created_utc", "run_id", "policy_version"]
    created_utc = _now()
    run_id = "run_pr19_synthetic"
    policy_version = "baseline"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        latest_dir = temp_root / "_latest"

        tournament_path = latest_dir / "tournament_latest.json"
        decision_path = latest_dir / "promotion_decision_latest.json"
        judge_path = latest_dir / "progress_judge_latest.json"
        history_path = latest_dir / "policy_history_latest.json"

        _write_json(
            tournament_path,
            {
                "schema_version": 1,
                "created_utc": created_utc,
                "run_id": run_id,
                "policy_version": policy_version,
                "entries": [],
            },
        )
        _write_json(
            decision_path,
            {
                "schema_version": 1,
                "created_utc": created_utc,
                "run_id": run_id,
                "policy_version": policy_version,
                "ts_utc": created_utc,
                "candidate_id": "candidate_1",
                "decision": "REJECT",
                "reasons": ["no_candidate_available"],
                "required_next_steps": ["collect_more_runs"],
            },
        )
        _write_json(
            judge_path,
            {
                "schema_version": "1.0",
                "created_utc": created_utc,
                "run_id": run_id,
                "policy_version": policy_version,
                "recommendation": "INSUFFICIENT_DATA",
                "scores": {"vs_do_nothing": None, "vs_buy_hold": None},
                "drivers": [],
                "not_improving_reasons": [],
                "suggested_next_actions": [],
                "trend": {"direction": "unknown", "window": 0, "values": []},
                "risk_metrics": {},
            },
        )
        _write_json(
            history_path,
            {
                "schema_version": 1,
                "created_utc": created_utc,
                "run_id": run_id,
                "policy_version": policy_version,
                "last_decision": {
                    "ts_utc": created_utc,
                    "decision": "REJECT",
                    "candidate_id": "candidate_1",
                    "reasons": ["no_candidate_available"],
                },
                "history_tail": [],
            },
        )

        artifacts = {
            "tournament": tournament_path,
            "promotion_decision": decision_path,
            "progress_judge_latest": judge_path,
            "policy_history": history_path,
        }

        for name, path in artifacts.items():
            if not path.exists():
                return False, f"missing_artifact:{name}"
            payload = json.loads(path.read_text(encoding="utf-8"))
            missing = _validate_required(payload, required_fields)
            if missing:
                return False, f"{name}_missing_fields:{','.join(missing)}"

        _ = load_progress_judge_latest(judge_path)
        _ = load_policy_history_latest(history_path)
        _ = load_engine_status(tournament_path, decision_path, judge_path)

    return True, "ok"


def main() -> int:
    failures: List[str] = []
    degraded = 0

    print("PR19_GATE_START")

    compile_targets = [
        TOOLS_DIR / "train_daemon.py",
        TOOLS_DIR / "progress_judge.py",
        TOOLS_DIR / "ui_parsers.py",
        TOOLS_DIR / "ui_app.py",
        TOOLS_DIR / "verify_consistency.py",
        TOOLS_DIR / "verify_pr19_gate.py",
    ]
    compile_ok, compile_msg = _run_py_compile(compile_targets)
    if not compile_ok:
        failures.append(f"py_compile_failed:{compile_msg}")

    hygiene_ok, hygiene_msg = _run_repo_hygiene()
    if not hygiene_ok:
        failures.append(f"repo_hygiene_failed:{hygiene_msg}")

    artifacts_ok, artifacts_msg = _synthetic_artifacts()
    if not artifacts_ok:
        failures.append(f"synthetic_artifacts_failed:{artifacts_msg}")

    status = "PASS" if not failures else "FAIL"
    summary = "|".join(
        [
            "PR19_GATE_SUMMARY",
            f"status={status}",
            f"degraded={degraded}",
            f"failed={len(failures)}",
            f"details={' ; '.join(failures) if failures else 'ok'}",
        ]
    )
    print(summary)
    print("PR19_GATE_END")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
