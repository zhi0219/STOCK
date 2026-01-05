from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from tools.pr28_training_loop import PR28Config, RUNS_ROOT, run_pr28_flow


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: List[str]


def _safe_read_json(path: Path) -> Dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _validate_required_fields(payload: Dict[str, object], required: Iterable[str]) -> ValidationResult:
    missing = [field for field in required if field not in payload]
    if missing:
        return ValidationResult(False, [f"missing:{','.join(missing)}"])
    return ValidationResult(True, [])


def _validate_artifact(path: Path, required: Iterable[str]) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, [f"missing_file:{path}"])
    payload = _safe_read_json(path)
    if not payload:
        return ValidationResult(False, [f"invalid_json:{path}"])
    return _validate_required_fields(payload, required)


def _validate_jsonl(path: Path, required: Iterable[str]) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, [f"missing_file:{path}"])
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return ValidationResult(False, [f"empty_jsonl:{path}"])
    try:
        payload = json.loads(lines[-1])
    except Exception:
        return ValidationResult(False, [f"invalid_jsonl:{path}"])
    if not isinstance(payload, dict):
        return ValidationResult(False, [f"invalid_jsonl_payload:{path}"])
    return _validate_required_fields(payload, required)


def _ensure_fail_closed() -> ValidationResult:
    missing_payload = {"schema_version": 1, "ts_utc": "now"}
    required = ["schema_version", "ts_utc", "run_id", "git_commit"]
    result = _validate_required_fields(missing_payload, required)
    if result.ok:
        return ValidationResult(False, ["missing_fields_not_detected"])
    return ValidationResult(True, [])


def main() -> int:
    runs_root = RUNS_ROOT / "_pr28_gate"
    config = PR28Config(
        runs_root=runs_root,
        seed=28,
        max_steps=40,
        candidate_count=2,
        min_steps=60,
        quotes_limit=80,
    )
    artifacts = run_pr28_flow(config)
    latest_dir = runs_root / "_latest"

    required_common = ["schema_version", "ts_utc", "run_id", "git_commit"]
    required_promotion = required_common + [
        "decision",
        "baseline_results",
        "trial_count",
        "candidate_count",
        "search_scale_penalty",
    ]
    errors: List[str] = []

    errors.extend(_validate_artifact(artifacts["tournament_result"], required_common).reasons)
    errors.extend(_validate_artifact(artifacts["judge_result"], required_common + ["status"]).reasons)
    errors.extend(_validate_artifact(artifacts["promotion_decision"], required_promotion).reasons)
    errors.extend(_validate_jsonl(artifacts["promotion_history"], required_common + ["decision"]).reasons)

    errors.extend(
        _validate_artifact(latest_dir / "tournament_result_latest.json", required_common).reasons
    )
    errors.extend(_validate_artifact(latest_dir / "judge_result_latest.json", required_common).reasons)
    errors.extend(
        _validate_artifact(latest_dir / "promotion_decision_latest.json", required_promotion).reasons
    )
    errors.extend(
        _validate_artifact(latest_dir / "promotion_history_latest.json", required_common).reasons
    )

    fail_closed = _ensure_fail_closed()
    errors.extend(fail_closed.reasons)

    if os.environ.get("PR28_FORCE_FAIL") == "1":
        errors.append("PR28_FORCE_FAIL")

    if errors:
        print("verify_pr28_gate FAIL")
        for reason in errors:
            print(f" - {reason}")
        return 1

    print("verify_pr28_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
