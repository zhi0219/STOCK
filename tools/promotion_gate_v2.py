from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from tools.paths import to_repo_relative

ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = ROOT / "Logs" / "train_runs"


@dataclass(frozen=True)
class GateConfig:
    max_drawdown_pct: float = 5.0
    max_turnover: int = 20
    max_reject_rate: float = 0.2
    window_count: int = 3
    window_passes_required: int = 2
    auto_promote_consecutive: int = 3
    require_walk_forward: bool = False
    require_no_lookahead: bool = False
    require_trade_activity: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def evaluate_safety(metrics: Dict[str, object], config: GateConfig) -> tuple[bool, List[str]]:
    failures: List[str] = []
    drawdown = float(metrics.get("max_drawdown_pct") or 0.0)
    turnover = int(metrics.get("turnover") or 0)
    reject_rate = float(metrics.get("reject_rate") or 0.0)
    if drawdown > config.max_drawdown_pct:
        failures.append(f"drawdown>{config.max_drawdown_pct:.2f}%")
    if turnover > config.max_turnover:
        failures.append(f"turnover>{config.max_turnover}")
    if reject_rate > config.max_reject_rate:
        failures.append(f"reject_rate>{config.max_reject_rate:.2f}")
    return not failures, failures


def _score(metrics: Dict[str, object]) -> float:
    score = metrics.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    equity = float(metrics.get("final_equity_usd") or 0.0)
    drawdown = float(metrics.get("max_drawdown_pct") or 0.0)
    turnover = int(metrics.get("turnover") or 0)
    rejects = int(metrics.get("num_rejects") or 0)
    return (equity - 10_000.0) / 100.0 - drawdown * 25.0 - turnover * 0.5 - rejects * 2.0


def _recent_decisions(candidate_id: str, limit: int) -> List[Dict[str, object]]:
    if not RUNS_ROOT.exists():
        return []
    decisions: List[Dict[str, object]] = []
    for day_dir in sorted(RUNS_ROOT.iterdir()):
        if not day_dir.is_dir():
            continue
        for run_dir in sorted(day_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            decision_path = run_dir / "promotion_decision.json"
            if not decision_path.exists():
                continue
            try:
                payload = json.loads(decision_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("candidate_id") != candidate_id:
                continue
            payload["run_id"] = run_dir.name
            decisions.append(payload)
    decisions.sort(key=lambda item: str(item.get("ts_utc", "")))
    return decisions[-limit:] if limit > 0 else decisions


def _count_consecutive_approvals(decisions: List[Dict[str, object]]) -> int:
    count = 0
    for entry in reversed(decisions):
        if entry.get("decision") == "APPROVE":
            count += 1
        else:
            break
    return count


def evaluate_promotion_gate(
    candidate: Dict[str, object] | None,
    baselines: List[Dict[str, object]],
    run_id: str,
    config: GateConfig | None = None,
    stress_report: Dict[str, object] | None = None,
    walk_forward_result: Dict[str, object] | None = None,
    no_lookahead_audit: Dict[str, object] | None = None,
    trade_activity_report: Dict[str, object] | None = None,
) -> Dict[str, object]:
    config = config or GateConfig()
    ts = _now()
    if not candidate:
        return {
            "ts_utc": ts,
            "candidate_id": None,
            "decision": "REJECT",
            "reasons": ["no_candidate_available"],
            "required_next_steps": ["select_candidate_with_metrics"],
            "evidence_run_ids": [run_id],
            "auto_promote_eligible": False,
        }

    candidate_id = str(candidate.get("candidate_id") or "unknown")
    reasons: List[str] = []
    required_steps: List[str] = []

    safety_pass, safety_failures = evaluate_safety(candidate, config)
    if not safety_pass:
        reasons.append("safety_constraints_failed")
        required_steps.append("reduce_drawdown_or_turnover")

    candidate_score = _score(candidate)
    baseline_scores = {str(b.get("candidate_id", "baseline")): _score(b) for b in baselines}
    beat_baselines = True
    for baseline_id, baseline_score in baseline_scores.items():
        if candidate_score <= baseline_score:
            beat_baselines = False
            reasons.append(f"baseline_not_beaten:{baseline_id}")

    decisions = _recent_decisions(candidate_id, config.window_count - 1)
    window_passes = sum(1 for entry in decisions if entry.get("decision") == "APPROVE")
    stress_ok = True
    stress_failures: List[str] = []
    stress_status = None
    stress_evidence: Dict[str, object] = {}
    if not stress_report:
        stress_ok = False
        stress_failures.append("stress_report_missing")
        required_steps.append("run_stress_harness")
    else:
        stress_status = stress_report.get("status")
        scenarios = stress_report.get("scenarios") if isinstance(stress_report.get("scenarios"), list) else []
        if not scenarios:
            stress_ok = False
            stress_failures.append("stress_scenarios_missing")
            required_steps.append("run_stress_harness")
        baseline_pass = bool(stress_report.get("baseline_pass"))
        stress_pass = bool(stress_report.get("stress_pass"))
        if not baseline_pass:
            stress_ok = False
            stress_failures.append("stress_baseline_failed")
        if not stress_pass:
            stress_ok = False
            stress_failures.append("stress_scenarios_failed")
        if stress_status not in {"PASS", "FAIL"}:
            stress_ok = False
            stress_failures.append("stress_status_missing")
        if stress_status == "FAIL":
            stress_ok = False
        report_failures = stress_report.get("fail_reasons")
        if isinstance(report_failures, list) and report_failures:
            stress_failures.extend(str(item) for item in report_failures)
        evidence = stress_report.get("evidence")
        if isinstance(evidence, dict):
            stress_evidence = {
                key: to_repo_relative(Path(str(value)))
                if isinstance(value, str)
                else value
                for key, value in evidence.items()
            }

    if stress_failures:
        reasons.append("stress_constraints_failed")
        required_steps.append("improve_stress_metrics")

    walk_forward_ok = True
    walk_forward_status = None
    walk_forward_failures: List[str] = []
    walk_forward_evidence: Dict[str, object] = {}
    if walk_forward_result is None:
        walk_forward_status = "MISSING"
        if config.require_walk_forward:
            walk_forward_ok = False
            walk_forward_failures.append("walk_forward_missing")
            required_steps.append("run_walk_forward_eval")
    else:
        walk_forward_status = str(walk_forward_result.get("status") or "UNKNOWN")
        window_passes = int(walk_forward_result.get("window_passes") or 0)
        window_required = int(walk_forward_result.get("window_passes_required") or 0)
        if walk_forward_status != "PASS":
            walk_forward_ok = False
            walk_forward_failures.append(f"walk_forward_status:{walk_forward_status}")
        if window_required and window_passes < window_required:
            walk_forward_ok = False
            walk_forward_failures.append(
                f"walk_forward_windows:{window_passes}/{window_required}"
            )
        evidence = {
            "result_path": walk_forward_result.get("result_path"),
            "windows_path": walk_forward_result.get("windows_path"),
        }
        walk_forward_evidence = {
            key: to_repo_relative(Path(str(value)))
            if isinstance(value, str)
            else value
            for key, value in evidence.items()
            if value
        }
        if walk_forward_failures:
            required_steps.append("improve_walk_forward_windows")

    no_lookahead_ok = True
    no_lookahead_status = None
    no_lookahead_issues: List[str] = []
    no_lookahead_evidence: Dict[str, object] = {}
    if no_lookahead_audit is None:
        no_lookahead_status = "MISSING"
        if config.require_no_lookahead:
            no_lookahead_ok = False
            no_lookahead_issues.append("no_lookahead_missing")
            required_steps.append("run_no_lookahead_audit")
    else:
        no_lookahead_status = str(no_lookahead_audit.get("status") or "UNKNOWN")
        issues = no_lookahead_audit.get("issues")
        if isinstance(issues, list):
            no_lookahead_issues = [str(item) for item in issues]
        if no_lookahead_status != "PASS":
            no_lookahead_ok = False
        evidence_path = no_lookahead_audit.get("result_path")
        if isinstance(evidence_path, str):
            no_lookahead_evidence = {
                "result_path": to_repo_relative(Path(evidence_path)),
            }
        if no_lookahead_issues:
            required_steps.append("resolve_no_lookahead_issues")

    if walk_forward_failures:
        reasons.append("walk_forward_constraints_failed")
    if no_lookahead_issues and config.require_no_lookahead:
        reasons.append("no_lookahead_constraints_failed")

    trade_activity_ok = True
    trade_activity_status = None
    trade_activity_violations: List[str] = []
    trade_activity_evidence: Dict[str, object] = {}
    if trade_activity_report is None:
        trade_activity_status = "MISSING"
        if config.require_trade_activity:
            trade_activity_ok = False
            trade_activity_violations.append("trade_activity_missing")
            required_steps.append("run_trade_activity_audit")
    else:
        trade_activity_status = str(trade_activity_report.get("status") or "UNKNOWN")
        violations = trade_activity_report.get("violations")
        if isinstance(violations, list):
            trade_activity_violations.extend(str(item.get("code", item)) for item in violations if item)
        if trade_activity_status != "PASS":
            trade_activity_ok = False
        if trade_activity_violations:
            trade_activity_ok = False
        evidence = trade_activity_report.get("evidence")
        if isinstance(evidence, dict):
            trade_activity_evidence = {
                key: to_repo_relative(Path(str(value)))
                if isinstance(value, str)
                else value
                for key, value in evidence.items()
                if value
            }
        if trade_activity_violations:
            required_steps.append("reduce_trade_activity")

    if trade_activity_violations or (trade_activity_status == "MISSING" and config.require_trade_activity):
        reasons.append("overtrading_constraints_failed")

    current_pass = bool(
        safety_pass
        and beat_baselines
        and stress_ok
        and walk_forward_ok
        and no_lookahead_ok
        and trade_activity_ok
    )
    total_passes = window_passes + (1 if current_pass else 0)
    if total_passes < config.window_passes_required:
        reasons.append("insufficient_window_wins")
        required_steps.append(
            f"achieve_{config.window_passes_required}_passes_in_last_{config.window_count}_windows"
        )

    decision = "APPROVE" if current_pass and total_passes >= config.window_passes_required else "REJECT"

    history_for_auto = decisions + [
        {"decision": "APPROVE" if current_pass else "REJECT", "ts_utc": ts}
    ]
    consecutive = _count_consecutive_approvals(history_for_auto)
    auto_eligible = decision == "APPROVE" and consecutive >= config.auto_promote_consecutive

    if decision == "APPROVE" and not reasons:
        reasons.append("risk_adjusted_outperformance")
    if decision == "REJECT" and not reasons:
        reasons.append("gate_rejected")
    if decision == "REJECT" and not required_steps:
        required_steps.append("collect_more_runs_for_gate")

    return {
        "ts_utc": ts,
        "candidate_id": candidate_id,
        "decision": decision,
        "reasons": reasons,
        "required_next_steps": required_steps,
        "evidence_run_ids": [run_id],
        "baseline_scores": baseline_scores,
        "candidate_score": candidate_score,
        "window_passes": total_passes,
        "window_required": config.window_passes_required,
        "window_count": config.window_count,
        "auto_promote_eligible": auto_eligible,
        "auto_promote_required_consecutive": config.auto_promote_consecutive,
        "safety_failures": safety_failures,
        "stress_status": stress_status,
        "stress_failures": stress_failures,
        "stress_evidence": stress_evidence,
        "walk_forward_status": walk_forward_status,
        "walk_forward_failures": walk_forward_failures,
        "walk_forward_evidence": walk_forward_evidence,
        "no_lookahead_status": no_lookahead_status,
        "no_lookahead_issues": no_lookahead_issues,
        "no_lookahead_evidence": no_lookahead_evidence,
        "trade_activity_status": trade_activity_status,
        "trade_activity_violations": trade_activity_violations,
        "trade_activity_evidence": trade_activity_evidence,
    }


def parse_args(argv: List[str]) -> object:
    import argparse

    parser = argparse.ArgumentParser(
        description="Promotion gate v2 evaluator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--candidate", help="Path to candidate metrics JSON")
    parser.add_argument("--baselines", help="Path to baselines metrics JSON")
    parser.add_argument("--stress-report", dest="stress_report", help="Path to stress report JSON")
    parser.add_argument(
        "--trade-activity-report",
        dest="trade_activity_report",
        help="Path to trade activity report JSON",
    )
    parser.add_argument("--run-id", dest="run_id", default="manual", help="Run ID for evidence")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    candidate = {}
    baselines: List[Dict[str, object]] = []
    stress_report: Dict[str, object] | None = None
    trade_activity_report: Dict[str, object] | None = None
    if args.candidate:
        candidate_path = Path(args.candidate)
        if candidate_path.exists():
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if args.baselines:
        baselines_path = Path(args.baselines)
        if baselines_path.exists():
            payload = json.loads(baselines_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                baselines = payload
    if args.stress_report:
        stress_path = Path(args.stress_report)
        if stress_path.exists():
            stress_payload = json.loads(stress_path.read_text(encoding="utf-8"))
            if isinstance(stress_payload, dict):
                stress_report = stress_payload
    if args.trade_activity_report:
        report_path = Path(args.trade_activity_report)
        if report_path.exists():
            report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(report_payload, dict):
                trade_activity_report = report_payload
    decision = evaluate_promotion_gate(
        candidate or None,
        baselines,
        str(args.run_id),
        stress_report=stress_report,
        trade_activity_report=trade_activity_report,
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0 if decision.get("decision") == "APPROVE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
