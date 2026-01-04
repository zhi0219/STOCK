from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from tools.execution_friction import load_friction_policy
from tools.paths import repo_root, to_repo_relative
from tools.sim_autopilot import run_step
from tools.sim_tournament import _variant_config

ROOT = repo_root()
DEFAULT_QUOTES = ROOT / "Data" / "quotes.csv"
FALLBACK_QUOTES = ROOT / "fixtures" / "quotes_sample.csv"


@dataclass
class VariantResult:
    variant: str
    metrics: Dict[str, float | int]
    score: float


def _load_quotes(path: Path, max_steps: int) -> List[Dict[str, object]]:
    quotes: List[Dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if max_steps and len(quotes) >= max_steps:
                break
            record: Dict[str, object] = {k: v for k, v in row.items() if v not in {None, ""}}
            try:
                record["price"] = float(record.get("price") or 0.0)
            except Exception:
                record["price"] = 0.0
            quotes.append(record)
    return quotes


def _score_run(metrics: Dict[str, float | int]) -> float:
    drawdown = float(metrics.get("max_drawdown_pct", 0.0))
    postmortems = int(metrics.get("num_postmortems", 0))
    risk_rejects = int(metrics.get("num_risk_rejects", 0))
    orders = int(metrics.get("num_orders", 0))
    final_equity = float(metrics.get("final_equity_usd", 0.0))
    return (
        -drawdown * 100.0
        - postmortems * 50.0
        - risk_rejects * 5.0
        - orders * 0.1
        + final_equity / 100.0
    )


def _simulate_variant(
    quotes: Iterable[Dict[str, object]],
    variant: str,
    friction_policy: Dict[str, float | int],
    logs_dir: Path,
) -> VariantResult:
    cfg = _variant_config(variant)
    sim_state: Dict[str, object] = {
        "cash_usd": 10_000.0,
        "risk_state": {
            "mode": "NORMAL",
            "equity": 10_000.0,
            "start_equity": 10_000.0,
            "peak_equity": 10_000.0,
        },
    }
    max_drawdown = 0.0
    orders = 0
    risk_rejects = 0
    postmortems = 0

    for row in quotes:
        sim_state, emitted = run_step(
            row,
            sim_state,
            {
                **cfg,
                "logs_dir": logs_dir,
                "policy_version": "baseline",
                "risk_overrides": cfg.get("risk_overrides", {}),
                "friction_policy": friction_policy,
                "verify_no_lookahead": True,
            },
        )
        for event in emitted:
            decision = str(event.get("decision") or "")
            if decision == "ALLOW" and abs(float(event.get("fill_qty", 0.0))) > 0:
                orders += 1
            elif decision in {"RISK_REJECT", "EXECUTION_REJECTED", "EXECUTION_FAILED"}:
                risk_rejects += 1
        risk_state = sim_state.get("risk_state", {}) or {}
        max_drawdown = max(max_drawdown, float(risk_state.get("drawdown", 0.0)) * 100.0)
        if risk_state.get("postmortem_triggered"):
            postmortems = 1

    final_equity = float(sim_state.get("risk_state", {}).get("equity", sim_state.get("cash_usd", 0.0)))
    metrics = {
        "final_equity_usd": round(final_equity, 2),
        "max_drawdown_pct": round(max_drawdown, 4),
        "num_orders": orders,
        "num_risk_rejects": risk_rejects,
        "num_postmortems": postmortems,
    }
    score = round(_score_run(metrics), 4)
    return VariantResult(variant=variant, metrics=metrics, score=score)


def _rank_variants(results: List[VariantResult]) -> List[VariantResult]:
    return sorted(results, key=lambda r: r.score, reverse=True)


def _build_diff_table(
    baseline: List[VariantResult], stressed: List[VariantResult]
) -> List[Dict[str, object]]:
    baseline_rank = {result.variant: idx + 1 for idx, result in enumerate(baseline)}
    stressed_rank = {result.variant: idx + 1 for idx, result in enumerate(stressed)}
    baseline_score = {result.variant: result.score for result in baseline}
    stressed_score = {result.variant: result.score for result in stressed}
    rows = []
    for variant in baseline_rank:
        rows.append(
            {
                "variant": variant,
                "baseline_rank": baseline_rank[variant],
                "stress_rank": stressed_rank.get(variant),
                "baseline_score": baseline_score.get(variant),
                "stress_score": stressed_score.get(variant),
                "rank_shift": abs(baseline_rank[variant] - stressed_rank.get(variant, baseline_rank[variant])),
                "score_delta": round(
                    (stressed_score.get(variant, 0.0) - baseline_score.get(variant, 0.0)), 4
                ),
            }
        )
    return rows


def _assess_sensitivity(diff_table: List[Dict[str, object]], baseline: List[VariantResult], stressed: List[VariantResult]) -> Tuple[bool, str]:
    if not baseline or not stressed:
        return False, "Missing baseline or stress results"
    top_change = baseline[0].variant != stressed[0].variant
    max_shift = max((int(row.get("rank_shift", 0)) for row in diff_table), default=0)
    total_shift = sum(int(row.get("rank_shift", 0)) for row in diff_table)
    drastic = top_change and (max_shift >= 2 or total_shift >= 3)
    if drastic:
        return False, f"Ranking instability detected (top_change={top_change}, max_shift={max_shift}, total_shift={total_shift})"
    return True, "Ranking stable under doubled friction"


def _double_friction(policy: Dict[str, float | int]) -> Dict[str, float | int]:
    doubled = dict(policy)
    for key in ("fee_per_trade", "fee_per_share", "spread_bps", "slippage_bps", "latency_ms", "gap_bps"):
        doubled[key] = float(doubled.get(key, 0.0)) * 2.0
    for prob_key in ("partial_fill_prob", "reject_prob", "fail_prob"):
        doubled[prob_key] = min(1.0, float(doubled.get(prob_key, 0.0)) * 2.0)
    return doubled


def _write_report(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify execution friction model", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--artifacts-dir", default="artifacts", help="Artifacts output directory")
    parser.add_argument("--input", default=str(DEFAULT_QUOTES), help="Quotes CSV input path")
    parser.add_argument("--max-steps", type=int, default=200, help="Max quote steps to evaluate")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    input_path = input_path.expanduser().resolve()
    input_source = "explicit"
    if not input_path.exists():
        if input_path == DEFAULT_QUOTES and FALLBACK_QUOTES.exists():
            input_path = FALLBACK_QUOTES
            input_source = "fallback_fixture"
        else:
            raise SystemExit(f"Input quotes not found: {input_path}")

    friction_policy = load_friction_policy()
    stress_policy = _double_friction(friction_policy)
    quotes = _load_quotes(input_path, int(args.max_steps))
    if not quotes:
        raise SystemExit(f"No quotes loaded from {input_path}")

    variants = ["baseline", "conservative", "aggressive"]
    baseline_results = [
        _simulate_variant(
            quotes,
            variant,
            friction_policy,
            artifacts_dir / "execution_model_runs" / "baseline" / variant,
        )
        for variant in variants
    ]
    stressed_results = [
        _simulate_variant(
            quotes,
            variant,
            stress_policy,
            artifacts_dir / "execution_model_runs" / "stress_x2" / variant,
        )
        for variant in variants
    ]

    baseline_ranked = _rank_variants(baseline_results)
    stressed_ranked = _rank_variants(stressed_results)
    diff_table = _build_diff_table(baseline_ranked, stressed_ranked)
    ok, reason = _assess_sensitivity(diff_table, baseline_ranked, stressed_ranked)

    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if ok else "FAIL",
        "reason": reason,
        "input_path": to_repo_relative(input_path),
        "input_source": input_source,
        "max_steps": int(args.max_steps),
        "variants": variants,
        "friction_policy": friction_policy,
        "stress_policy": stress_policy,
        "baseline_ranking": [
            {"variant": result.variant, "score": result.score, "metrics": result.metrics}
            for result in baseline_ranked
        ],
        "stress_ranking": [
            {"variant": result.variant, "score": result.score, "metrics": result.metrics}
            for result in stressed_ranked
        ],
        "diff_table": diff_table,
    }

    report_path = artifacts_dir / "execution_model_report.json"
    text_path = artifacts_dir / "execution_model_report.txt"
    _write_report(report_path, report)
    lines = [
        "EXECUTION_MODEL_REPORT",
        f"status={report['status']}",
        f"reason={reason}",
        f"input={report['input_path']}",
        f"baseline_top={baseline_ranked[0].variant if baseline_ranked else 'n/a'}",
        f"stress_top={stressed_ranked[0].variant if stressed_ranked else 'n/a'}",
        "diff_table:",
    ]
    for row in diff_table:
        lines.append(
            f"- {row['variant']}: baseline_rank={row['baseline_rank']} stress_rank={row['stress_rank']} "
            f"score_delta={row['score_delta']} rank_shift={row['rank_shift']}"
        )
    lines.append(f"report_path={to_repo_relative(report_path)}")
    _write_text(text_path, lines)
    print(f"EXECUTION_MODEL_SUMMARY|status={report['status']}|reason={reason}|report={to_repo_relative(report_path)}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
