from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.execution_friction import load_friction_policy
from tools.fs_atomic import atomic_write_json
from tools.overtrading_calibration import load_overtrading_calibration, select_overtrading_budget
from tools.overtrading_budget import load_overtrading_budget
from tools.paths import repo_root, to_repo_relative
from tools.regime_classifier import build_report as build_regime_report
from tools.regime_classifier import write_regime_report

ROOT = repo_root()
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
ARTIFACTS_DIR = ROOT / "artifacts"
LATEST_DIR = RUNS_ROOT / "_latest"
LATEST_REPLAY_INDEX = LATEST_DIR / "replay_index_latest.json"
LATEST_TRADE_ACTIVITY = LATEST_DIR / "trade_activity_report_latest.json"

SCHEMA_VERSION = 1


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


def _safe_read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    except Exception:
        return []
    return rows


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


def _relpath(path: Path | None) -> str | None:
    if path is None:
        return None
    return to_repo_relative(path)


def _find_latest_replay_index() -> tuple[Path | None, dict[str, str]]:
    if LATEST_REPLAY_INDEX.exists():
        return LATEST_REPLAY_INDEX, {"mode": "latest_pointer", "path": _relpath(LATEST_REPLAY_INDEX) or ""}

    candidates = []
    if RUNS_ROOT.exists():
        for run_dir in RUNS_ROOT.iterdir():
            if not run_dir.is_dir() or run_dir.name.startswith("_"):
                continue
            replay_index = run_dir / "replay" / "replay_index.json"
            if replay_index.exists():
                candidates.append(replay_index)
    if not candidates:
        return None, {"mode": "missing", "path": "Logs/train_runs/*/replay/replay_index.json"}
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest, {"mode": "scan", "path": _relpath(latest) or ""}


def _resolve_run_dir(replay_index_path: Path | None, run_dir: Path | None) -> Path | None:
    if run_dir is not None:
        return run_dir
    if replay_index_path is None:
        return None
    if replay_index_path.parent.name == "replay":
        return replay_index_path.parent.parent
    if replay_index_path.parent.name == "_latest":
        return replay_index_path.parent.parent
    return replay_index_path.parent


def _load_decision_cards(replay_index_payload: dict[str, Any], replay_index_path: Path | None) -> tuple[list[dict[str, Any]], Path | None]:
    pointers = replay_index_payload.get("pointers") if isinstance(replay_index_payload.get("pointers"), dict) else {}
    decision_rel = pointers.get("decision_cards") if isinstance(pointers, dict) else None
    decision_path = None
    if isinstance(decision_rel, str) and decision_rel:
        decision_path = ROOT / decision_rel
    elif replay_index_path is not None:
        candidate = replay_index_path.parent / "decision_cards.jsonl"
        if candidate.exists():
            decision_path = candidate
    if decision_path is None or not decision_path.exists():
        return [], decision_path
    return _safe_read_jsonl(decision_path), decision_path


def _load_orders(run_dir: Path | None) -> tuple[list[dict[str, Any]], Path | None]:
    if run_dir is None:
        return [], None
    orders_path = run_dir / "orders_sim.jsonl"
    if not orders_path.exists():
        return [], orders_path
    return _safe_read_jsonl(orders_path), orders_path


def _collect_trade_events(orders: list[dict[str, Any]], decision_cards: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    line_map: dict[int, int] = {}
    if orders:
        for idx, order in enumerate(orders, start=1):
            ts = _parse_ts(order.get("ts_utc"))
            qty = float(order.get("fill_qty") or order.get("qty") or 0.0)
            price = float(order.get("fill_price") or order.get("price") or 0.0)
            events.append(
                {
                    "ts": ts,
                    "qty": qty,
                    "price": price,
                    "pnl": order.get("pnl"),
                    "fee_usd": order.get("fee_usd") or order.get("sim_fill", {}).get("fee_usd"),
                    "line": idx,
                }
            )
            line_map[idx] = idx
    else:
        for idx, card in enumerate(decision_cards, start=1):
            action = str(card.get("action") or "").upper()
            if action not in {"BUY", "SELL"}:
                continue
            ts = _parse_ts(card.get("ts_utc"))
            qty = float(card.get("size") or 0.0)
            if action == "SELL" and qty > 0:
                qty = -qty
            price_snapshot = card.get("price_snapshot") if isinstance(card.get("price_snapshot"), dict) else {}
            price = float(price_snapshot.get("last") or 0.0)
            events.append(
                {
                    "ts": ts,
                    "qty": qty,
                    "price": price,
                    "pnl": card.get("pnl_delta"),
                    "fee_usd": None,
                    "line": idx,
                }
            )
            line_map[idx] = idx
    return {"events": events, "line_map": line_map}


def _trade_activity_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    trades_total = len(events)
    timestamps = [entry.get("ts") for entry in events if entry.get("ts")]
    timestamps = [ts for ts in timestamps if isinstance(ts, datetime)]

    trades_per_day = None
    trades_per_day_peak = None
    trades_per_hour_peak = None
    min_seconds_between = None
    span_days = None
    if timestamps:
        timestamps.sort()
        day_buckets: dict[str, int] = {}
        hour_buckets: dict[str, int] = {}
        for ts in timestamps:
            day_key = ts.date().isoformat()
            hour_key = ts.replace(minute=0, second=0, microsecond=0).isoformat()
            day_buckets[day_key] = day_buckets.get(day_key, 0) + 1
            hour_buckets[hour_key] = hour_buckets.get(hour_key, 0) + 1
        trades_per_day_peak = max(day_buckets.values()) if day_buckets else 0
        trades_per_day = trades_total / max(1, len(day_buckets)) if day_buckets else 0
        trades_per_hour_peak = max(hour_buckets.values()) if hour_buckets else 0
        span_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
        span_days = span_seconds / 86400.0 if span_seconds >= 0 else None
        for earlier, later in zip(timestamps, timestamps[1:]):
            delta = (later - earlier).total_seconds()
            if delta < 0:
                continue
            if min_seconds_between is None or delta < min_seconds_between:
                min_seconds_between = delta

    gross_turnover = 0.0
    net_turnover = 0.0
    for entry in events:
        qty = float(entry.get("qty") or 0.0)
        price = float(entry.get("price") or 0.0)
        notional = qty * price
        gross_turnover += abs(notional)
        net_turnover += notional

    avg_holding_time = None
    holding_samples: list[float] = []
    holding_weights: list[float] = []
    open_lots: dict[str, list[tuple[float, datetime]]] = {"__default__": []}
    for entry in events:
        ts = entry.get("ts")
        if not isinstance(ts, datetime):
            continue
        qty = float(entry.get("qty") or 0.0)
        if qty == 0:
            continue
        lots = open_lots.setdefault("__default__", [])
        if qty > 0:
            lots.append((qty, ts))
        else:
            remaining = abs(qty)
            while remaining > 0 and lots:
                lot_qty, lot_ts = lots[0]
                close_qty = min(remaining, lot_qty)
                holding_samples.append((ts - lot_ts).total_seconds())
                holding_weights.append(close_qty)
                remaining -= close_qty
                if close_qty >= lot_qty:
                    lots.pop(0)
                else:
                    lots[0] = (lot_qty - close_qty, lot_ts)
    if holding_samples and holding_weights:
        weighted = sum(sample * weight for sample, weight in zip(holding_samples, holding_weights))
        total_weight = sum(holding_weights)
        if total_weight > 0:
            avg_holding_time = weighted / total_weight

    churn_score = None
    if span_days is not None:
        churn_score = trades_total / max(1.0, span_days)

    return {
        "trades_total": trades_total,
        "trades_per_day": trades_per_day,
        "trades_per_day_peak": trades_per_day_peak,
        "trades_per_hour_peak": trades_per_hour_peak,
        "min_seconds_between_trades": min_seconds_between,
        "span_days": span_days,
        "turnover_gross": gross_turnover,
        "turnover_net": abs(net_turnover),
        "avg_holding_time_seconds": avg_holding_time,
        "churn_score": churn_score,
    }


def _estimate_costs(events: list[dict[str, Any]], friction_policy: dict[str, Any]) -> dict[str, Any]:
    trades_total = len(events)
    fees = []
    pnl_values = []
    for entry in events:
        fee = entry.get("fee_usd")
        if isinstance(fee, (int, float)):
            fees.append(float(fee))
        pnl = entry.get("pnl")
        if isinstance(pnl, (int, float)):
            pnl_values.append(float(pnl))
    if fees:
        estimated_cost_total = sum(fees)
    else:
        estimated_cost_total = float(friction_policy.get("fee_per_trade") or 0.0) * trades_total

    cost_per_trade = estimated_cost_total / trades_total if trades_total else 0.0
    edge_after_cost: float | str
    if pnl_values and len(pnl_values) == trades_total:
        edge_after_cost = sum(pnl_values)
    else:
        edge_after_cost = "INSUFFICIENT_DATA"

    return {
        "estimated_cost_total": estimated_cost_total,
        "cost_per_trade": cost_per_trade,
        "edge_after_cost": edge_after_cost,
    }


def _format_violation(
    code: str,
    threshold: Any,
    observed: Any,
    evidence_paths: Iterable[str] | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "threshold": threshold,
        "observed": observed,
        "evidence_paths": list(evidence_paths or []),
    }
    if detail:
        payload["detail"] = detail
    return payload


def _detect_violations(
    events: list[dict[str, Any]],
    metrics: dict[str, Any],
    budget_payload: dict[str, Any],
    orders_path: Path | None,
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    budget = budget_payload.get("budget", {}) if isinstance(budget_payload.get("budget"), dict) else {}

    max_trades_per_day = budget.get("max_trades_per_day")
    if isinstance(max_trades_per_day, (int, float)):
        peak = metrics.get("trades_per_day_peak")
        if isinstance(peak, (int, float)) and peak > float(max_trades_per_day):
            violations.append(
                _format_violation(
                    "max_trades_per_day",
                    max_trades_per_day,
                    peak,
                    evidence_paths=[_relpath(orders_path) or ""],
                )
            )

    min_seconds = budget.get("min_seconds_between_trades")
    if isinstance(min_seconds, (int, float)):
        observed = metrics.get("min_seconds_between_trades")
        if isinstance(observed, (int, float)) and observed < float(min_seconds):
            violations.append(
                _format_violation(
                    "min_seconds_between_trades",
                    min_seconds,
                    round(observed, 2),
                    evidence_paths=[_relpath(orders_path) or ""],
                )
            )

    max_turnover = budget.get("max_turnover_per_day")
    if isinstance(max_turnover, (int, float)):
        peak_turnover = None
        if events:
            day_totals: dict[str, float] = {}
            for entry in events:
                ts = entry.get("ts")
                if not isinstance(ts, datetime):
                    continue
                day_key = ts.date().isoformat()
                qty = float(entry.get("qty") or 0.0)
                price = float(entry.get("price") or 0.0)
                day_totals[day_key] = day_totals.get(day_key, 0.0) + abs(qty * price)
            if day_totals:
                peak_turnover = max(day_totals.values())
        if isinstance(peak_turnover, (int, float)) and peak_turnover > float(max_turnover):
            violations.append(
                _format_violation(
                    "max_turnover_per_day",
                    max_turnover,
                    round(peak_turnover, 4),
                    evidence_paths=[_relpath(orders_path) or ""],
                )
            )

    max_cost = budget.get("max_cost_per_trade")
    if isinstance(max_cost, (int, float)):
        cost_per_trade = metrics.get("cost_per_trade")
        if isinstance(cost_per_trade, (int, float)) and cost_per_trade > float(max_cost):
            violations.append(
                _format_violation(
                    "max_cost_per_trade",
                    max_cost,
                    round(cost_per_trade, 4),
                    evidence_paths=[_relpath(orders_path) or ""],
                )
            )

    if budget_payload.get("status") != "PASS":
        violations.append(
            _format_violation(
                "overtrading_budget_missing",
                "seed_required",
                budget_payload.get("missing_reasons"),
                evidence_paths=[budget_payload.get("sources", {}).get("seed", "")],
            )
        )

    return violations


def build_report(
    *,
    replay_index_path: Path | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    created_utc = _now_iso()
    missing_reasons: list[str] = []

    source = {"mode": "unknown", "path": ""}
    if replay_index_path is None and run_dir is None:
        replay_index_path, source = _find_latest_replay_index()
    if run_dir is not None and replay_index_path is None:
        source = {"mode": "run_dir", "path": _relpath(run_dir) or ""}

    replay_index_payload = _safe_read_json(replay_index_path) if replay_index_path else None
    if replay_index_path and not replay_index_payload:
        missing_reasons.append("replay_index_unreadable")

    if replay_index_path is None and run_dir is None:
        missing_reasons.append("replay_index_missing")

    resolved_run_dir = _resolve_run_dir(replay_index_path, run_dir)

    decision_cards, decision_path = ([], None)
    if replay_index_payload:
        decision_cards, decision_path = _load_decision_cards(replay_index_payload, replay_index_path)

    orders, orders_path = _load_orders(resolved_run_dir)
    if not orders and not decision_cards:
        missing_reasons.append("trade_inputs_missing")

    trade_events = _collect_trade_events(orders, decision_cards)
    events = trade_events["events"]
    metrics = _trade_activity_metrics(events)

    friction_policy = load_friction_policy()
    cost_metrics = _estimate_costs(events, friction_policy)
    metrics = dict(metrics)
    metrics["cost_per_trade"] = cost_metrics.get("cost_per_trade")

    regime_report = build_regime_report(replay_index_path=replay_index_path, run_dir=run_dir)
    regime_outputs = write_regime_report(regime_report, resolved_run_dir, None, None)
    regime_report_path = None
    if regime_outputs:
        regime_report_path = regime_outputs.get("latest") or regime_outputs.get("run_report")
    regime_label = str(regime_report.get("label") or "INSUFFICIENT_DATA")

    budget_payload = load_overtrading_budget()
    base_budget = budget_payload.get("budget") if isinstance(budget_payload.get("budget"), dict) else {}
    calibration_payload = load_overtrading_calibration()
    selected_budget, calibration_info = select_overtrading_budget(base_budget, calibration_payload, regime_label)
    budget_payload = dict(budget_payload)
    budget_payload["budget"] = selected_budget
    budget_payload["calibration"] = calibration_info

    violations = _detect_violations(events, metrics, budget_payload, orders_path)

    status = "PASS"
    if missing_reasons or violations:
        status = "FAIL"

    report = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": created_utc,
        "run_id": replay_index_payload.get("run_id") if replay_index_payload else (resolved_run_dir.name if resolved_run_dir else None),
        "status": status,
        "missing_reasons": missing_reasons,
        "trades_total": metrics.get("trades_total"),
        "trades_per_day": metrics.get("trades_per_day"),
        "trades_per_day_peak": metrics.get("trades_per_day_peak"),
        "trades_per_hour_peak": metrics.get("trades_per_hour_peak"),
        "min_seconds_between_trades": metrics.get("min_seconds_between_trades"),
        "turnover_gross": metrics.get("turnover_gross"),
        "turnover_net": metrics.get("turnover_net"),
        "avg_holding_time_seconds": metrics.get("avg_holding_time_seconds"),
        "churn_score": metrics.get("churn_score"),
        "span_days": metrics.get("span_days"),
        "estimated_cost_total": cost_metrics.get("estimated_cost_total"),
        "cost_per_trade": cost_metrics.get("cost_per_trade"),
        "edge_after_cost": cost_metrics.get("edge_after_cost"),
        "violations": violations,
        "budget": budget_payload,
        "regime": {
            "label": regime_report.get("label"),
            "status": regime_report.get("status"),
            "metrics": regime_report.get("metrics"),
            "missing_reasons": regime_report.get("missing_reasons", []),
            "evidence": regime_report.get("evidence"),
            "source": regime_report.get("source"),
            "report_path": _relpath(regime_report_path),
        },
        "calibration": budget_payload.get("calibration", {}),
        "source": source,
        "evidence": {
            "replay_index": _relpath(replay_index_path),
            "decision_cards": _relpath(decision_path),
            "orders_sim": _relpath(orders_path),
            "friction_policy": _relpath(ROOT / "Data" / "friction_policy.json"),
            "overtrading_budget_seed": budget_payload.get("sources", {}).get("seed"),
            "overtrading_budget_runtime": budget_payload.get("sources", {}).get("runtime"),
            "overtrading_calibration": budget_payload.get("calibration", {}).get("latest_path"),
            "regime_report": _relpath(regime_report_path),
        },
    }
    return report


def write_trade_activity_report(
    report: dict[str, Any],
    run_dir: Path | None,
    artifacts_output: Path | None,
) -> dict[str, Path]:
    outputs: dict[str, Path] = {}
    if run_dir is not None:
        report_path = run_dir / "trade_activity_report.json"
        atomic_write_json(report_path, report)
        outputs["run_report"] = report_path
    latest_dir = LATEST_DIR
    if run_dir is not None and run_dir.parent.exists() and run_dir.parent.name.startswith("_"):
        latest_dir = run_dir.parent / "_latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    latest_path = latest_dir / "trade_activity_report_latest.json"
    atomic_write_json(latest_path, report)
    outputs["latest"] = latest_path

    if artifacts_output is not None:
        atomic_write_json(artifacts_output, report)
        outputs["artifacts"] = artifacts_output
    return outputs


def _should_write_artifacts(default: Path | None) -> Path | None:
    if default is not None:
        return default
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return ARTIFACTS_DIR / "trade_activity_report.json"
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trade activity audit (SIM-only, read-only)")
    parser.add_argument("--replay-index", help="Path to replay_index.json or replay_index_latest.json")
    parser.add_argument("--run-dir", help="Run directory containing orders_sim.jsonl")
    parser.add_argument("--artifacts-output", help="Optional artifacts output path")
    parser.add_argument("--no-artifacts-output", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    replay_index_path = Path(args.replay_index) if args.replay_index else None
    run_dir = Path(args.run_dir) if args.run_dir else None
    if replay_index_path and not replay_index_path.is_absolute():
        replay_index_path = (ROOT / replay_index_path).resolve()
    if run_dir and not run_dir.is_absolute():
        run_dir = (ROOT / run_dir).resolve()

    artifacts_output = None
    if not args.no_artifacts_output:
        artifacts_output = (
            Path(args.artifacts_output)
            if args.artifacts_output
            else _should_write_artifacts(None)
        )
        if artifacts_output and not artifacts_output.is_absolute():
            artifacts_output = (ROOT / artifacts_output).resolve()

    print("TRADE_ACTIVITY_AUDIT_START")
    report = build_report(replay_index_path=replay_index_path, run_dir=run_dir)
    outputs = write_trade_activity_report(report, run_dir, artifacts_output)
    summary = (
        f"TRADE_ACTIVITY_AUDIT_SUMMARY|status={report.get('status')}|"
        f"violations={len(report.get('violations', []))}|"
        f"turnover={report.get('turnover_gross')}|trades_total={report.get('trades_total')}"
    )
    print(summary)
    print("TRADE_ACTIVITY_AUDIT_END")

    if report.get("status") != "PASS":
        missing = report.get("missing_reasons", [])
        if missing:
            print(f"missing_reasons={','.join(str(item) for item in missing)}")
        if outputs:
            print(f"report_paths={','.join(_relpath(path) or '' for path in outputs.values())}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_report", "write_trade_activity_report"]
