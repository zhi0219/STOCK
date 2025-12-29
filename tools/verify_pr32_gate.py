from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from tools.doctor_report import build_report, write_report
from tools.no_lookahead_audit import AuditConfig, run_audit
from tools.pr28_training_loop import PR28Config, RUNS_ROOT, run_pr28_flow
from tools.promotion_gate_v2 import GateConfig, evaluate_promotion_gate
from tools.walk_forward_eval import WalkForwardConfig, run_walk_forward
from tools.write_xp_snapshot import write_xp_snapshot
from tools.xp_model import compute_xp_snapshot

ARTIFACTS_DIR = Path("artifacts")
XP_SNAPSHOT_ARTIFACT = ARTIFACTS_DIR / "xp_snapshot.json"
ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\")


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    except Exception:
        return None
    return rows


def _compare_key_fields(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("xp_spec_version", "xp_total", "level"):
        if left.get(key) != right.get(key):
            errors.append(f"mismatch:{key}")
    left_breakdown = [
        (item.get("key"), item.get("points"))
        for item in left.get("xp_breakdown", [])
        if isinstance(item, dict)
    ]
    right_breakdown = [
        (item.get("key"), item.get("points"))
        for item in right.get("xp_breakdown", [])
        if isinstance(item, dict)
    ]
    if left_breakdown != right_breakdown:
        errors.append("mismatch:breakdown_keys_points")
    return errors


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


def _assert_repo_relative(snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    evidence_paths: list[str] = []
    for item in snapshot.get("xp_breakdown", []):
        if isinstance(item, dict):
            paths = item.get("evidence_paths_rel")
            if isinstance(paths, list):
                evidence_paths.extend([str(p) for p in paths])
    source_artifacts = snapshot.get("source_artifacts", {})
    if isinstance(source_artifacts, dict):
        evidence_paths.extend([str(p) for p in source_artifacts.values()])
    for path in evidence_paths:
        if _contains_absolute_path(path):
            errors.append(f"absolute_path_detected:{path}")
    return errors


def _assert_walk_forward_required() -> list[str]:
    errors: list[str] = []
    config = GateConfig(require_walk_forward=True, walk_forward_min_windows=2, walk_forward_min_pass_rate=0.5)
    candidate = {
        "candidate_id": "wf_candidate",
        "score": 1.2,
        "max_drawdown_pct": 1.0,
        "turnover": 2,
        "reject_rate": 0.0,
    }
    baselines = [{"candidate_id": "baseline", "score": 0.1, "max_drawdown_pct": 1.0}]
    decision = evaluate_promotion_gate(candidate, baselines, "pr32_gate", config, walk_forward=None)
    if decision.get("decision") != "REJECT":
        errors.append("promotion_gate_not_fail_closed")
    reasons = decision.get("reasons") if isinstance(decision.get("reasons"), list) else []
    if "walk_forward_missing" not in reasons:
        errors.append("promotion_gate_missing_reason")
    return errors


def _assert_no_lookahead_pass(audit_payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if audit_payload.get("status") != "PASS":
        errors.append("no_lookahead_audit_failed")
    return errors


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
    artifacts = run_pr28_flow(config)

    walk_forward_config = WalkForwardConfig(
        runs_root=runs_root,
        windows=2,
        train_size=20,
        eval_size=10,
        seed=32,
        max_steps=20,
        candidate_count=1,
        min_pass_rate=0.4,
        min_baseline_beats=1,
        min_windows_required=2,
        artifacts_dir=ARTIFACTS_DIR,
    )
    wf_paths = run_walk_forward(walk_forward_config)

    audit_payload = run_audit(AuditConfig(rows=20, lookback=3, artifacts_dir=ARTIFACTS_DIR))
    errors.extend(_assert_no_lookahead_pass(audit_payload))

    doctor_report = build_report()
    write_report(doctor_report, ARTIFACTS_DIR / "doctor_report.json")

    write_xp_snapshot(runs_root=runs_root, artifacts_output=XP_SNAPSHOT_ARTIFACT)

    if not XP_SNAPSHOT_ARTIFACT.exists():
        errors.append("xp_snapshot_missing")
    snapshot = _safe_read_json(XP_SNAPSHOT_ARTIFACT)
    if not snapshot:
        errors.append("xp_snapshot_invalid_json")

    if snapshot:
        evidence_paths = {
            "tournament": artifacts.get("tournament_result"),
            "judge": artifacts.get("judge_result"),
            "promotion": artifacts.get("promotion_decision"),
            "promotion_history": artifacts.get("promotion_history_latest"),
            "promotion_history_jsonl": artifacts.get("promotion_history"),
            "walk_forward": wf_paths.get("latest_result"),
            "doctor_report": ARTIFACTS_DIR / "doctor_report.json",
            "repo_hygiene": ARTIFACTS_DIR / "repo_hygiene.json",
        }
        tournament_payload = _safe_read_json(Path(str(artifacts.get("tournament_result"))))
        judge_payload = _safe_read_json(Path(str(artifacts.get("judge_result"))))
        promotion_payload = _safe_read_json(Path(str(artifacts.get("promotion_decision"))))
        promotion_history_payload = _safe_read_json(Path(str(artifacts.get("promotion_history_latest"))))
        history_events = _safe_read_jsonl(Path(str(artifacts.get("promotion_history"))))
        walk_forward_payload = _safe_read_json(Path(str(wf_paths.get("latest_result"))))
        doctor_payload = _safe_read_json(ARTIFACTS_DIR / "doctor_report.json")
        repo_hygiene_payload = _safe_read_json(ARTIFACTS_DIR / "repo_hygiene.json")

        recomputed = compute_xp_snapshot(
            tournament=tournament_payload,
            judge=judge_payload,
            promotion=promotion_payload,
            promotion_history=promotion_history_payload,
            promotion_history_events=history_events,
            walk_forward=walk_forward_payload,
            doctor_report=doctor_payload,
            repo_hygiene=repo_hygiene_payload,
            evidence_paths=evidence_paths,
            created_utc=str(snapshot.get("created_utc") or ""),
            run_id=str(snapshot.get("run_id") or ""),
        )
        errors.extend(_compare_key_fields(snapshot, recomputed))
        errors.extend(_assert_repo_relative(snapshot))

    errors.extend(_assert_walk_forward_required())

    if not Path(str(wf_paths.get("walk_forward_result"))).exists():
        errors.append("walk_forward_result_missing")
    if not Path(str(wf_paths.get("walk_forward_windows"))).exists():
        errors.append("walk_forward_windows_missing")

    if os.environ.get("PR32_FORCE_FAIL") == "1":
        errors.append("PR32_FORCE_FAIL")

    if errors:
        print("verify_pr32_gate FAIL")
        for reason in errors:
            print(f" - {reason}")
        return 1

    print("verify_pr32_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
