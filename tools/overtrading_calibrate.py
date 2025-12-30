from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.overtrading_budget import DEFAULT_BUDGET
from tools.paths import repo_root, to_repo_relative, walk_forward_latest_dir
from tools.regime_classifier import build_report as build_regime_report
from tools.trade_activity_audit import build_report as build_trade_activity_report

ROOT = repo_root()
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
ARTIFACTS_DIR = ROOT / "artifacts"
LATEST_DIR = RUNS_ROOT / "_latest"

SCHEMA_VERSION = 1
DEFAULT_MIN_SAMPLES = 5
DEFAULT_MAX_RUNS = 30


@dataclass(frozen=True)
class CalibrationSample:
    run_id: str
    created_utc: str
    regime_label: str
    trades_per_day_peak: float | None
    trades_per_day: float | None
    turnover_per_day: float | None
    cooldown_violations: int
    cost_per_trade: float | None
    min_seconds_between_trades: float | None
    evidence: dict[str, str | None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _sort_key(sample: CalibrationSample) -> datetime:
    parsed = _parse_ts(sample.created_utc)
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    bounded = sorted(values)
    if pct <= 0:
        return bounded[0]
    if pct >= 100:
        return bounded[-1]
    rank = (len(bounded) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(bounded) - 1)
    if lower == upper:
        return bounded[lower]
    weight = rank - lower
    return bounded[lower] * (1 - weight) + bounded[upper] * weight


def _summary(values: list[float]) -> dict[str, Any]:
    values_sorted = sorted(values)
    return {
        "count": len(values_sorted),
        "min": values_sorted[0] if values_sorted else None,
        "max": values_sorted[-1] if values_sorted else None,
        "mean": sum(values_sorted) / len(values_sorted) if values_sorted else None,
        "median": _percentile(values_sorted, 50),
        "p10": _percentile(values_sorted, 10),
        "p90": _percentile(values_sorted, 90),
        "values": values_sorted,
    }


def _should_write_artifacts(default: Path | None) -> Path | None:
    if default is not None:
        return default
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return ARTIFACTS_DIR / "overtrading_calibration.json"
    return None


def _complete_runs(runs_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    runs: list[tuple[Path, dict[str, Any]]] = []
    for run_complete_path in runs_root.glob("**/run_complete.json"):
        payload = _safe_read_json(run_complete_path)
        if not payload:
            continue
        if payload.get("status") != "complete":
            continue
        run_dir = run_complete_path.parent
        runs.append((run_dir, payload))
    return runs


def _collect_samples(runs_root: Path, max_runs: int) -> list[CalibrationSample]:
    samples: list[CalibrationSample] = []
    for run_dir, run_complete in _complete_runs(runs_root):
        run_id = str(run_complete.get("run_id") or run_dir.name)
        created_utc = str(run_complete.get("created_utc") or run_complete.get("ts_utc") or "")

        trade_report_path = run_dir / "trade_activity_report.json"
        trade_report = _safe_read_json(trade_report_path)
        if not trade_report:
            trade_report = build_trade_activity_report(run_dir=run_dir)
        if not trade_report:
            continue

        regime_report = build_regime_report(run_dir=run_dir)
        regime_label = str(regime_report.get("label") or "INSUFFICIENT_DATA")

        violations = trade_report.get("violations", [])
        cooldown_violations = 0
        if isinstance(violations, list):
            cooldown_violations = sum(
                1
                for item in violations
                if isinstance(item, dict) and item.get("code") == "min_seconds_between_trades"
            )

        span_days = trade_report.get("span_days")
        turnover_gross = trade_report.get("turnover_gross")
        turnover_per_day = None
        if isinstance(span_days, (int, float)) and span_days > 0 and isinstance(turnover_gross, (int, float)):
            turnover_per_day = float(turnover_gross) / float(span_days)

        evidence = {
            "run_dir": to_repo_relative(run_dir),
            "trade_activity_report": to_repo_relative(trade_report_path),
            "replay_index": str(regime_report.get("evidence", {}).get("replay_index") or ""),
            "decision_cards": str(regime_report.get("evidence", {}).get("decision_cards") or ""),
            "stress_report": to_repo_relative(run_dir / "stress_report.json") if (run_dir / "stress_report.json").exists() else None,
        }

        samples.append(
            CalibrationSample(
                run_id=run_id,
                created_utc=created_utc,
                regime_label=regime_label,
                trades_per_day_peak=trade_report.get("trades_per_day_peak")
                if isinstance(trade_report.get("trades_per_day_peak"), (int, float))
                else None,
                trades_per_day=trade_report.get("trades_per_day")
                if isinstance(trade_report.get("trades_per_day"), (int, float))
                else None,
                turnover_per_day=turnover_per_day,
                cooldown_violations=int(cooldown_violations),
                cost_per_trade=trade_report.get("cost_per_trade")
                if isinstance(trade_report.get("cost_per_trade"), (int, float))
                else None,
                min_seconds_between_trades=trade_report.get("min_seconds_between_trades")
                if isinstance(trade_report.get("min_seconds_between_trades"), (int, float))
                else None,
                evidence=evidence,
            )
        )

    samples.sort(key=_sort_key)
    return samples[-max_runs:] if max_runs > 0 else samples


def _group_by_regime(samples: Iterable[CalibrationSample]) -> dict[str, list[CalibrationSample]]:
    grouped: dict[str, list[CalibrationSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.regime_label, []).append(sample)
    return grouped


def _recommended_budget(samples: list[CalibrationSample]) -> dict[str, Any]:
    trades_peak = [s.trades_per_day_peak for s in samples if isinstance(s.trades_per_day_peak, (int, float))]
    turnover = [s.turnover_per_day for s in samples if isinstance(s.turnover_per_day, (int, float))]
    min_seconds = [s.min_seconds_between_trades for s in samples if isinstance(s.min_seconds_between_trades, (int, float))]
    cost = [s.cost_per_trade for s in samples if isinstance(s.cost_per_trade, (int, float))]
    return {
        "max_trades_per_day": _percentile([float(v) for v in trades_peak], 90) if trades_peak else None,
        "max_turnover_per_day": _percentile([float(v) for v in turnover], 90) if turnover else None,
        "min_seconds_between_trades": _percentile([float(v) for v in min_seconds], 10) if min_seconds else None,
        "max_cost_per_trade": _percentile([float(v) for v in cost], 90) if cost else None,
    }


def _regime_entry(samples: list[CalibrationSample], min_samples: int) -> dict[str, Any]:
    trades = [float(s.trades_per_day_peak) for s in samples if isinstance(s.trades_per_day_peak, (int, float))]
    turnover = [float(s.turnover_per_day) for s in samples if isinstance(s.turnover_per_day, (int, float))]
    cooldown = [float(s.cooldown_violations) for s in samples]
    cost = [float(s.cost_per_trade) for s in samples if isinstance(s.cost_per_trade, (int, float))]
    min_seconds = [float(s.min_seconds_between_trades) for s in samples if isinstance(s.min_seconds_between_trades, (int, float))]

    insufficient = len(samples) < min_samples
    insufficient_reasons = []
    if insufficient:
        insufficient_reasons.append(f"sample_size_below_min:{len(samples)}<{min_samples}")

    return {
        "sample_size": len(samples),
        "distributions": {
            "trades_per_day_peak": _summary(trades),
            "turnover_per_day": _summary(turnover),
            "cooldown_violations": _summary(cooldown),
            "cost_per_trade": _summary(cost),
            "min_seconds_between_trades": _summary(min_seconds),
        },
        "recommended_budget": _recommended_budget(samples),
        "insufficient_data": insufficient,
        "insufficient_reasons": insufficient_reasons,
        "run_ids": [s.run_id for s in samples],
        "evidence": [s.evidence for s in samples],
    }


def build_calibration(
    *,
    runs_root: Path = RUNS_ROOT,
    max_runs: int = DEFAULT_MAX_RUNS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    created_utc = _now_iso()
    samples = _collect_samples(runs_root, max_runs)
    grouped = _group_by_regime(samples)

    regimes: dict[str, Any] = {}
    for regime_label, regime_samples in grouped.items():
        regimes[regime_label] = _regime_entry(regime_samples, min_samples)

    sample_dates = [s.created_utc for s in samples if s.created_utc]
    date_range = {
        "start": min(sample_dates) if sample_dates else None,
        "end": max(sample_dates) if sample_dates else None,
    }

    insufficient_any = any(entry.get("insufficient_data") for entry in regimes.values()) if regimes else True
    status = "INSUFFICIENT_DATA" if insufficient_any else "OK"

    walk_forward_result = walk_forward_latest_dir() / "walk_forward_result_latest.json"
    walk_forward_windows = walk_forward_latest_dir() / "walk_forward_windows_latest.jsonl"
    friction_policy_path = ROOT / "Data" / "friction_policy.json"
    input_artifacts = {
        "friction_policy": to_repo_relative(friction_policy_path) if friction_policy_path.exists() else None,
        "walk_forward_result": to_repo_relative(walk_forward_result) if walk_forward_result.exists() else None,
        "walk_forward_windows": to_repo_relative(walk_forward_windows) if walk_forward_windows.exists() else None,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": created_utc,
        "status": status,
        "sample_size": len(samples),
        "min_samples_per_regime": min_samples,
        "max_runs_considered": max_runs,
        "date_range": date_range,
        "run_ids": [s.run_id for s in samples],
        "regimes": regimes,
        "assumptions": [
            "Budgets derived from observed distributions: P90 caps for trades/turnover/cost, P10 for min_seconds_between_trades.",
            "Trades/day uses per-run peak trades_per_day_peak for guardrail alignment.",
            "Regimes labeled via tools.regime_classifier on replay decision-card prices.",
        ],
        "input_artifacts": input_artifacts,
        "default_budget": DEFAULT_BUDGET,
        "repo_root_rel": str(ROOT.name),
    }


def write_calibration(
    payload: dict[str, Any],
    artifacts_output: Path | None,
    latest_output: Path | None,
) -> dict[str, Path]:
    outputs: dict[str, Path] = {}
    if artifacts_output is not None:
        artifacts_output.parent.mkdir(parents=True, exist_ok=True)
        artifacts_output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        outputs["artifacts"] = artifacts_output
    if latest_output is not None:
        latest_output.parent.mkdir(parents=True, exist_ok=True)
        latest_output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        outputs["latest"] = latest_output
    return outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overtrading calibration harness (SIM-only, read-only)")
    parser.add_argument("--runs-root", default=str(RUNS_ROOT), help="Runs root under Logs/train_runs")
    parser.add_argument("--max-runs", type=int, default=DEFAULT_MAX_RUNS)
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument("--artifacts-output", help="Optional artifacts output path")
    parser.add_argument("--latest-output", help="Optional latest output path")
    parser.add_argument("--no-artifacts-output", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = (ROOT / runs_root).resolve()

    artifacts_output = None
    latest_output = None
    if not args.no_artifacts_output:
        artifacts_output = (
            Path(args.artifacts_output)
            if args.artifacts_output
            else _should_write_artifacts(None)
        )
        if artifacts_output and not artifacts_output.is_absolute():
            artifacts_output = (ROOT / artifacts_output).resolve()
        latest_output = (
            Path(args.latest_output)
            if args.latest_output
            else (LATEST_DIR / "overtrading_calibration_latest.json")
        )
        if latest_output and not latest_output.is_absolute():
            latest_output = (ROOT / latest_output).resolve()

    payload = build_calibration(runs_root=runs_root, max_runs=args.max_runs, min_samples=args.min_samples)
    paths: dict[str, str] = {}
    if artifacts_output is not None:
        paths["artifacts"] = to_repo_relative(artifacts_output)
    if latest_output is not None:
        paths["latest"] = to_repo_relative(latest_output)
    if paths:
        payload["paths"] = paths
    outputs = write_calibration(payload, artifacts_output, latest_output)

    status = payload.get("status")
    print(f"OVERTRADING_CALIBRATE_SUMMARY|status={status}|samples={payload.get('sample_size')}")
    if outputs:
        print(
            "report_paths="
            + ",".join(to_repo_relative(path) for path in outputs.values() if path is not None)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_calibration"]
