from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from tools.execution_friction import load_friction_policy
from tools.paths import repo_root, to_repo_relative
from tools.promotion_gate_v2 import GateConfig, evaluate_safety
from tools.sim_autopilot import run_step

ROOT = repo_root()
RUNS_ROOT = ROOT / "Logs" / "train_runs"


def _load_quotes(path: Path, limit: int | None = None) -> List[Dict[str, object]]:
    quotes: List[Dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if limit is not None and len(quotes) >= limit:
                break
            snapshot: Dict[str, object] = {k: v for k, v in row.items() if v not in {None, ""}}
            try:
                snapshot["price"] = float(snapshot.get("price") or 0.0)
            except Exception:
                snapshot["price"] = 0.0
            quotes.append(snapshot)
    return quotes


def _apply_multipliers(policy: Dict[str, float | int], multipliers: Dict[str, float]) -> Dict[str, float | int]:
    adjusted = dict(policy)
    adjusted["fee_per_trade"] = float(adjusted.get("fee_per_trade", 0.0)) * multipliers.get("fees", 1.0)
    adjusted["fee_per_share"] = float(adjusted.get("fee_per_share", 0.0)) * multipliers.get("fees", 1.0)
    adjusted["slippage_bps"] = float(adjusted.get("slippage_bps", 0.0)) * multipliers.get("slippage", 1.0)
    adjusted["spread_bps"] = float(adjusted.get("spread_bps", 0.0)) * multipliers.get("spread", 1.0)
    adjusted["latency_ms"] = float(adjusted.get("latency_ms", 0.0)) * multipliers.get("latency", 1.0)
    return adjusted


def _simulate_scenario(
    quotes: List[Dict[str, object]],
    policy_version: str,
    policy_cfg: Dict[str, object],
    friction_policy: Dict[str, float | int],
    run_dir: Path,
    scenario: str,
    seed: int | None,
    max_steps: int,
) -> Dict[str, object]:
    sim_state: Dict[str, object] = {
        "cash_usd": 10_000.0,
        "risk_state": {
            "mode": "NORMAL",
            "equity": 10_000.0,
            "start_equity": 10_000.0,
            "peak_equity": 10_000.0,
        },
    }
    trade_count = 0
    reject_count = 0
    peak = 10_000.0
    max_drawdown_pct = 0.0

    logs_dir = run_dir / "stress_runs" / scenario.lower()
    logs_dir.mkdir(parents=True, exist_ok=True)

    for idx, row in enumerate(quotes[: max_steps if max_steps > 0 else len(quotes)], start=1):
        sim_state, emitted = run_step(
            row,
            sim_state,
            {
                "logs_dir": logs_dir,
                "momentum_threshold_pct": 0.5,
                "verify_no_lookahead": True,
                "policy_version": policy_version,
                "risk_overrides": policy_cfg.get("risk_overrides", {}),
                "friction_policy": friction_policy,
                "friction_seed": seed,
            },
        )
        for event in emitted:
            if event.get("decision"):
                if event.get("decision") == "ALLOW":
                    if abs(float(event.get("fill_qty", 0.0))) > 0:
                        trade_count += 1
                else:
                    reject_count += 1
        risk_state = sim_state.get("risk_state", {}) or {}
        equity = float(risk_state.get("equity", sim_state.get("cash_usd", 0.0)))
        peak = max(peak, equity)
        drawdown_pct = ((peak - equity) / peak * 100.0) if peak else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

    final_equity = float(sim_state.get("risk_state", {}).get("equity", sim_state.get("cash_usd", 0.0)))
    return_pct = ((final_equity - 10_000.0) / 10_000.0) * 100.0
    turnover = trade_count
    reject_rate = reject_count / max(1, trade_count)
    return {
        "final_equity_usd": round(final_equity, 2),
        "return_pct": round(return_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "turnover": turnover,
        "reject_rate": round(reject_rate, 4),
        "steps": min(len(quotes), max_steps) if max_steps > 0 else len(quotes),
    }


def evaluate_stress(
    quotes: List[Dict[str, object]],
    policy_version: str,
    policy_cfg: Dict[str, object],
    run_dir: Path,
    seed: int,
    max_steps: int = 200,
) -> Dict[str, object]:
    base_policy = load_friction_policy()
    scenarios = [
        ("BASELINE", {"fees": 1.0, "slippage": 1.0, "spread": 1.0, "latency": 1.0}, None),
        ("STRESS_A", {"fees": 2.0, "slippage": 1.0, "spread": 1.0, "latency": 1.0}, None),
        ("STRESS_B", {"fees": 1.0, "slippage": 3.0, "spread": 2.0, "latency": 1.0}, None),
        ("STRESS_C", {"fees": 1.0, "slippage": 1.0, "spread": 1.0, "latency": 2.0}, seed + 303),
    ]

    scenario_rows: List[Dict[str, object]] = []
    overall_failures: List[str] = []
    gate_config = GateConfig()

    for name, multipliers, scenario_seed in scenarios:
        adjusted_policy = _apply_multipliers(base_policy, multipliers)
        if name == "STRESS_C":
            adjusted_policy["partial_fill_prob"] = max(float(adjusted_policy.get("partial_fill_prob", 0.0)), 0.35)
            adjusted_policy["max_fill_fraction"] = min(float(adjusted_policy.get("max_fill_fraction", 1.0)), 0.6)
        metrics = _simulate_scenario(
            quotes,
            policy_version,
            policy_cfg,
            adjusted_policy,
            run_dir,
            name,
            scenario_seed,
            max_steps,
        )
        safety_pass, failures = evaluate_safety(metrics, gate_config)
        if not safety_pass:
            overall_failures.append(f"{name}:" + ",".join(failures))
        scenario_rows.append(
            {
                "scenario": name,
                "multipliers": multipliers,
                "metrics": metrics,
                "pass": safety_pass,
                "failures": failures,
                "seed": scenario_seed,
            }
        )

    baseline_pass = next((row.get("pass") for row in scenario_rows if row.get("scenario") == "BASELINE"), False)
    stress_pass = all(row.get("pass") for row in scenario_rows if row.get("scenario") != "BASELINE")
    overall_pass = bool(baseline_pass and stress_pass)

    report_path = run_dir / "stress_report.json"
    scenarios_path = run_dir / "stress_scenarios.jsonl"

    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_dir.name,
        "policy_version": policy_version,
        "status": "PASS" if overall_pass else "FAIL",
        "overall_pass": overall_pass,
        "baseline_pass": baseline_pass,
        "stress_pass": stress_pass,
        "fail_reasons": overall_failures,
        "scenarios": scenario_rows,
        "evidence": {
            "report_path": to_repo_relative(report_path),
            "scenarios_path": to_repo_relative(scenarios_path),
            "friction_policy_path": to_repo_relative(ROOT / "Data" / "friction_policy.json"),
            "stress_seed": seed,
        },
    }

    run_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with scenarios_path.open("w", encoding="utf-8") as fh:
        for row in scenario_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    latest_dir = RUNS_ROOT / "_latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "stress_report_latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (latest_dir / "stress_scenarios_latest.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in scenario_rows) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stress harness for SIM execution friction",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default=str(ROOT / "Data" / "quotes.csv"), help="Quotes CSV input")
    parser.add_argument("--run-dir", required=True, help="Run directory to write stress artifacts")
    parser.add_argument("--policy-version", default="baseline", dest="policy_version")
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--max-steps", type=int, default=200)
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    input_path = input_path.expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input quotes not found: {input_path}")

    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    run_dir = run_dir.expanduser().resolve()

    quotes = _load_quotes(input_path, limit=args.max_steps)
    report = evaluate_stress(
        quotes,
        args.policy_version,
        {},
        run_dir,
        seed=args.seed,
        max_steps=args.max_steps,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("overall_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
