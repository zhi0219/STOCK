from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

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
    walk_forward_min_windows: int = 2
    walk_forward_min_pass_rate: float = 0.5


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


def _evaluate_walk_forward(
    walk_forward: Dict[str, object] | None,
    config: GateConfig,
) -> tuple[bool, List[str], Dict[str, object]]:
    reasons: List[str] = []
    summary = {
        "status": None,
        "window_count": None,
        "pass_rate": None,
    }
    if not walk_forward or not isinstance(walk_forward, dict):
        reasons.append("walk_forward_missing")
        return False, reasons, summary

    status = walk_forward.get("status")
    window_count = int(walk_forward.get("window_count") or 0)
    pass_rate = float(walk_forward.get("pass_rate") or 0.0)

    summary.update({"status": status, "window_count": window_count, "pass_rate": pass_rate})

    if str(status) == "INSUFFICIENT_DATA":
        reasons.append("walk_forward_insufficient")
    if window_count < config.walk_forward_min_windows:
        reasons.append("walk_forward_insufficient")
    if pass_rate < config.walk_forward_min_pass_rate:
        reasons.append("walk_forward_failed")
    if str(status) == "FAIL":
        reasons.append("walk_forward_failed")
    return not reasons, reasons, summary


def evaluate_promotion_gate(
    candidate: Dict[str, object] | None,
    baselines: List[Dict[str, object]],
    run_id: str,
    config: GateConfig | None = None,
    walk_forward: Dict[str, object] | None = None,
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
    walk_forward_pass = True
    walk_forward_reasons: List[str] = []
    walk_forward_summary: Dict[str, object] = {}
    if config.require_walk_forward:
        walk_forward_pass, walk_forward_reasons, walk_forward_summary = _evaluate_walk_forward(
            walk_forward, config
        )
        if not walk_forward_pass:
            reasons.extend(walk_forward_reasons)
            required_steps.append("run_walk_forward_eval")

    current_pass = bool(safety_pass and beat_baselines and walk_forward_pass)
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
        "walk_forward_required": config.require_walk_forward,
        "walk_forward": walk_forward_summary,
        "walk_forward_reasons": walk_forward_reasons,
    }


def parse_args(argv: List[str]) -> object:
    import argparse

    parser = argparse.ArgumentParser(
        description="Promotion gate v2 evaluator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--candidate", help="Path to candidate metrics JSON")
    parser.add_argument("--baselines", help="Path to baselines metrics JSON")
    parser.add_argument("--run-id", dest="run_id", default="manual", help="Run ID for evidence")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    candidate = {}
    baselines: List[Dict[str, object]] = []
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
    decision = evaluate_promotion_gate(candidate or None, baselines, str(args.run_id))
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0 if decision.get("decision") == "APPROVE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
