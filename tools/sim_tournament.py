from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

if str(Path(__file__).resolve().parent.parent) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.policy_registry import get_policy
from tools.sim_autopilot import run_step

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "Data" / "quotes.csv"
RUNS_DIR = ROOT / "Logs" / "tournament_runs"
REPORTS_DIR = ROOT / "Reports"


class TournamentError(Exception):
    pass


def _parse_ts(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception as exc:  # pragma: no cover - defensive
        raise TournamentError(f"Invalid timestamp: {value}") from exc


def _iter_rows(path: Path) -> Iterable[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            yield {k: v for k, v in row.items()}


def _build_windows_from_stride(start_ts: str, end_ts: str, stride: int) -> List[Tuple[datetime, datetime]]:
    start = _parse_ts(start_ts)
    end = _parse_ts(end_ts)
    if end < start:
        raise TournamentError("end_ts must be after start_ts")
    if stride <= 0:
        raise TournamentError("stride must be positive")
    windows: List[Tuple[datetime, datetime]] = []
    current = start
    while current <= end:
        window_end = min(end, current + timedelta(days=stride - 1))
        windows.append((current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def _parse_windows(raw: str) -> List[Tuple[datetime, datetime]]:
    windows: List[Tuple[datetime, datetime]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ".." not in chunk:
            raise TournamentError(f"Invalid window format: {chunk}")
        start_s, end_s = chunk.split("..", 1)
        windows.append((_parse_ts(start_s.strip()), _parse_ts(end_s.strip())))
    return windows


def _write_jsonl(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_portfolio(path: Path, state: Dict[str, object], step: int, ts_utc: str) -> None:
    body = {
        "ts_utc": ts_utc,
        "step": step,
        "cash_usd": state.get("cash_usd", 0.0),
        "positions": state.get("positions", {}),
        "avg_cost": state.get("avg_cost", {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def _score_run(metrics: Dict[str, float | int]) -> float:
    # Risk-first: drawdown > postmortem > orders > final equity
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


def _variant_config(name: str) -> Dict[str, object]:
    presets = {
        "baseline": {"momentum_threshold_pct": 0.5, "risk_overrides": {}},
        "conservative": {
            "momentum_threshold_pct": 0.9,
            "risk_overrides": {"max_orders_per_minute": 1, "max_drawdown": 0.03},
        },
        "aggressive": {
            "momentum_threshold_pct": 0.35,
            "risk_overrides": {"max_orders_per_minute": 3, "max_drawdown": 0.08},
        },
    }
    return presets.get(name, presets["baseline"])


def _load_quotes(input_path: Path) -> List[Dict[str, object]]:
    quotes: List[Dict[str, object]] = []
    for row in _iter_rows(input_path):
        record: Dict[str, object] = {k: v for k, v in row.items() if v not in {None, ""}}
        try:
            record["price"] = float(record.get("price") or 0.0)
        except Exception:
            record["price"] = 0.0
        quotes.append(record)
    return quotes


def _within_window(row: Dict[str, object], start: datetime, end: datetime) -> bool:
    raw_ts = row.get("ts_utc") or row.get("ts")
    if not raw_ts:
        return False
    try:
        ts = datetime.fromisoformat(str(raw_ts))
    except Exception:
        return False
    return start <= ts <= end


def _safe_read_jsonl(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        return []
    rows: List[Dict[str, object]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _summarize_run(run_dir: Path) -> Dict[str, object]:
    equity_rows = _safe_read_jsonl(run_dir / "equity_curve.jsonl")
    orders_path = run_dir / "orders_sim.jsonl"
    events_path = run_dir / "events.jsonl"
    orders_count = 0
    if orders_path.exists():
        with orders_path.open("r", encoding="utf-8") as fh:
            orders_count = sum(1 for _ in fh if _.strip())
    risk_rejects = 0
    postmortems = 0
    for event in _safe_read_jsonl(events_path):
        if event.get("event_type") == "SIM_DECISION" and event.get("decision") == "RISK_REJECT":
            risk_rejects += 1
        if str(event.get("event_type")) == "POSTMORTEM":
            postmortems += 1
        if event.get("decision") == "POSTMORTEM":
            postmortems += 1
    final_equity = equity_rows[-1]["equity_usd"] if equity_rows else 0.0
    max_drawdown = max((row.get("drawdown_pct") or 0.0 for row in equity_rows), default=0.0)
    metrics: Dict[str, float | int] = {
        "final_equity_usd": final_equity,
        "max_drawdown_pct": max_drawdown,
        "num_orders": orders_count,
        "num_risk_rejects": risk_rejects,
        "num_postmortems": postmortems,
    }
    metrics["score"] = _score_run(metrics)
    return metrics


def _append_guard_proposal(run_dir: Path, reason: str, policy_version: str) -> None:
    event = {
        "event_type": "GUARD_PROPOSAL",
        "message": "Guardrail proposal for risky behavior",
        "proposal": reason,
        "policy_version": policy_version,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }
    for fname in ("events.jsonl", "events_sim.jsonl"):
        path = run_dir / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _run_single(
    quotes: Sequence[Dict[str, object]],
    window: Tuple[datetime, datetime],
    variant: str,
    max_steps: int,
    policy_version: str,
    policy_overrides: Dict[str, object],
) -> Tuple[str, Dict[str, object]]:
    start, end = window
    run_id = f"{policy_version}_{variant}_{start.date()}_{end.date()}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    cfg = _variant_config(variant)
    merged_overrides = dict(policy_overrides)
    merged_overrides.update(cfg.get("risk_overrides") or {})
    sim_state: Dict[str, object] = {
        "cash_usd": 10_000.0,
        "risk_state": {
            "mode": "NORMAL",
            "equity": 10_000.0,
            "start_equity": 10_000.0,
            "peak_equity": 10_000.0,
        },
    }
    eq_path = run_dir / "equity_curve.jsonl"
    portfolio_path = run_dir / "portfolio_sim.json"
    steps = 0
    for row in quotes:
        if not _within_window(row, start, end):
            continue
        sim_state, _ = run_step(
            row,
            sim_state,
            {**cfg, "logs_dir": run_dir, "policy_version": policy_version, "risk_overrides": merged_overrides},
        )
        risk_state = sim_state.get("risk_state", {}) or {}
        equity = float(risk_state.get("equity", sim_state.get("cash_usd", 0.0)))
        cash = float(sim_state.get("cash_usd", 0.0))
        drawdown_pct = float(risk_state.get("drawdown", 0.0)) * 100 if "drawdown" in risk_state else 0.0
        ts_utc_raw = row.get("ts_utc") or row.get("ts")
        try:
            ts_utc = datetime.fromisoformat(str(ts_utc_raw)).astimezone(timezone.utc)
        except Exception:
            ts_utc = datetime.now(timezone.utc)
        _write_jsonl(
            eq_path,
            {
                "ts_utc": ts_utc.isoformat(),
                "equity_usd": round(equity, 2),
                "cash_usd": round(cash, 2),
                "drawdown_pct": round(drawdown_pct, 4),
                "mode": risk_state.get("mode", "UNKNOWN"),
                "step": steps + 1,
                "policy_version": policy_version,
            },
        )
        _write_portfolio(portfolio_path, sim_state, steps + 1, ts_utc.isoformat())
        steps += 1
        if steps >= max_steps:
            break

    events_src = run_dir / "events_sim.jsonl"
    if events_src.exists():
        (run_dir / "events.jsonl").write_text(events_src.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    metrics = _summarize_run(run_dir)
    metrics.update(
        {
            "variant": variant,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "run_id": run_id,
        }
    )
    return run_id, metrics


def _render_report(runs: List[Dict[str, object]], path: Path) -> None:
    lines = ["# Sim Tournament v1", "", f"Generated: {datetime.now(timezone.utc).isoformat()} UTC", ""]
    sorted_runs = sorted(runs, key=lambda r: r.get("score", 0.0), reverse=True)
    lines.append("## Top performers (risk-first)")
    lines.append("")
    for idx, run in enumerate(sorted_runs[:3], start=1):
        lines.append(
            f"{idx}. {run['run_id']} | score={run['score']:.2f} | drawdown={run['max_drawdown_pct']:.2f}% | equity=${run['final_equity_usd']:.2f}"
        )
    lines.append("")
    lines.append("## Worst cases (needs guard review)")
    lines.append("")
    for idx, run in enumerate(sorted_runs[-3:], start=1):
        lines.append(
            f"{idx}. {run['run_id']} | score={run['score']:.2f} | drawdown={run['max_drawdown_pct']:.2f}% | rejects={run['num_risk_rejects']}"
        )
    lines.append("")
    lines.append("## Next steps")
    lines.append("- Review guard proposals on worst cases; tighten rate limits or stale-data gates as suggested.")
    lines.append("- Re-run with adjusted risk_overrides to validate improvements.")
    lines.append("- Keep simulation only; no real trading endpoints are touched.")
    lines.append("")
    if runs:
        example = runs[0]
        lines.append("### Re-run command (copy/paste)")
        lines.append(
            f".\\.venv\\Scripts\\python.exe tools\\sim_tournament.py --input Data\\quotes.csv --windows \"{example['window_start'].split('T')[0]}..{example['window_end'].split('T')[0]}\" --variants \"{example['variant']}\" --max-steps {max(int(example.get('steps', 0)), 10)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sim tournament runner", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Input quotes CSV path")
    parser.add_argument("--windows", help="Comma-separated windows start..end")
    parser.add_argument("--start-ts", dest="start_ts", help="Start timestamp for auto windows")
    parser.add_argument("--end-ts", dest="end_ts", help="End timestamp for auto windows")
    parser.add_argument("--stride", type=int, help="Stride in days for auto windows")
    parser.add_argument("--variants", default="baseline,conservative,aggressive", help="Comma separated variant names")
    parser.add_argument("--max-steps", type=int, default=250, dest="max_steps", help="Max steps per window")
    parser.add_argument("--policy-version", dest="policy_version", help="Policy version override", default=None)
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    input_path = input_path.expanduser().resolve()
    if not input_path.exists():
        raise TournamentError(f"Input not found: {input_path}")

    if args.windows:
        windows = _parse_windows(args.windows)
    else:
        if not (args.start_ts and args.end_ts and args.stride):
            raise TournamentError("Either --windows or (--start-ts, --end-ts, --stride) is required")
        windows = _build_windows_from_stride(args.start_ts, args.end_ts, int(args.stride))

    variants = [v.strip() for v in str(args.variants).split(",") if v.strip()]
    if not variants:
        variants = ["baseline"]

    policy_version, policy_cfg = get_policy(args.policy_version)
    quotes = _load_quotes(input_path)
    runs: List[Dict[str, object]] = []
    for window in windows:
        for variant in variants:
            run_id, metrics = _run_single(
                quotes,
                window,
                variant,
                int(args.max_steps),
                policy_version,
                policy_cfg.get("risk_overrides", {}),
            )
            metrics["steps"] = int(args.max_steps)
            metrics["policy_version"] = policy_version
            runs.append(metrics)

    sorted_runs = sorted(runs, key=lambda r: r.get("score", 0.0), reverse=True)
    worst_runs = sorted_runs[-3:]
    for run in worst_runs:
        reason = "Elevated drawdown or rejects; propose tighter stale-data gate and lower max_notional."
        _append_guard_proposal(RUNS_DIR / run["run_id"], reason, policy_version=policy_version)

    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "runs": runs, "policy_version": policy_version}
    summary_path = RUNS_DIR / f"tournament_summary_{policy_version}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report_name = f"tournament_{policy_version}_{datetime.now().strftime('%Y%m%d')}.md"
    report_path = REPORTS_DIR / report_name
    _render_report(runs, report_path)

    print(f"Wrote summary to {summary_path}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
