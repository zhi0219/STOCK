from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

import yaml

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.policy_registry import get_policy
from tools.sim_autopilot import _kill_switch_enabled, _kill_switch_path, run_step
from tools.sim_tournament import _load_quotes


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "Data" / "quotes.csv"
RUNS_ROOT = ROOT / "Logs" / "train_runs"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iter_rows(quotes: List[Dict[str, object]]) -> Iterable[Dict[str, object]]:
    for row in quotes:
        yield row


def _calc_drawdown(equities: List[float]) -> float:
    peak = equities[0] if equities else 0.0
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        if peak <= 0:
            continue
        max_dd = max(max_dd, (peak - eq) / peak)
    return max_dd


def _write_equity_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames = ["ts_utc", "equity_usd", "cash_usd", "drawdown_pct", "step", "policy_version", "mode"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def _log_size_mb(root: Path) -> float:
    total = 0
    if not root.exists():
        return 0.0
    for item in root.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total / (1024 * 1024)


def _summary_md(
    run_id: str,
    policy_version: str,
    equity_rows: List[Dict[str, object]],
    trade_count: int,
    rejects: Counter,
    stop_reason: str,
    outputs: Dict[str, Path],
) -> str:
    equities = [float(row.get("equity_usd", 0.0)) for row in equity_rows]
    net_change = (equities[-1] - equities[0]) if len(equities) >= 2 else 0.0
    max_dd = _calc_drawdown(equities) * 100 if equities else 0.0
    best = max(equity_rows, key=lambda r: r.get("equity_usd", 0.0), default=None)
    worst = min(equity_rows, key=lambda r: r.get("equity_usd", 0.0), default=None)
    lines = [
        "# Train Daemon Summary",
        "",
        f"Run: {run_id}",
        f"Policy: {policy_version}",
        f"Stop reason: {stop_reason}",
        f"Net value change: {net_change:+.2f} USD",
        f"Max drawdown: {max_dd:.2f}%",
        f"Trades executed: {trade_count}",
        "",
        "## Rejection reasons (top 5)",
    ]
    for reason, count in rejects.most_common(5):
        lines.append(f"- {reason}: {count}")
    if not rejects:
        lines.append("- None recorded")

    lines.append("")
    lines.append("## Best/Worst equity points")
    if best:
        lines.append(
            f"- Best: step {best.get('step')} at {best.get('ts_utc')} (equity_curve.csv)"
        )
    if worst:
        lines.append(
            f"- Worst: step {worst.get('step')} at {worst.get('ts_utc')} (equity_curve.csv)"
        )
    if not best and not worst:
        lines.append("- No equity points recorded")

    lines.append("")
    lines.append("## Outputs")
    for label, path in outputs.items():
        lines.append(f"- {label}: {path}")
    lines.append("")
    lines.append("All runs are SIM-only; no live trading endpoints are touched.")
    return "\n".join(lines)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SIM-only overnight training daemon",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Quotes CSV for simulation")
    parser.add_argument("--runs-root", default=str(RUNS_ROOT), help="Root directory for run outputs")
    parser.add_argument("--max-runtime-seconds", type=int, default=3600, dest="max_runtime_seconds")
    parser.add_argument("--max-steps", type=int, default=5000, dest="max_steps")
    parser.add_argument("--max-trades", type=int, default=500, dest="max_trades")
    parser.add_argument("--max-log-mb", type=float, default=128.0, dest="max_log_mb")
    parser.add_argument("--policy-version", dest="policy_version", help="Policy version override")
    parser.add_argument("--momentum-threshold", type=float, default=0.5, dest="momentum_threshold")
    parser.add_argument("--nightly", action="store_true", help="Preset for overnight runs (8h budget)")
    parser.add_argument("--seed", type=int, default=None, help="Seed for deterministic sampling")
    return parser.parse_args(argv)


def _apply_nightly_defaults(args: argparse.Namespace) -> None:
    if args.nightly:
        args.max_runtime_seconds = max(args.max_runtime_seconds, 8 * 60 * 60)
        args.max_steps = max(args.max_steps, 50_000)
        args.max_trades = max(args.max_trades, 10_000)
        args.max_log_mb = max(args.max_log_mb, 512.0)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    _apply_nightly_defaults(args)

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    input_path = input_path.expanduser().resolve()
    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}")
        return 1

    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = ROOT / runs_root
    runs_root = runs_root.expanduser().resolve()
    runs_root.mkdir(parents=True, exist_ok=True)

    policy_version, policy_cfg = get_policy(args.policy_version)
    quotes = _load_quotes(input_path)
    if not quotes:
        print("ERROR: no quotes available for simulation")
        return 1

    seed = args.seed if args.seed is not None else random.randint(1, 1_000_000)
    random.seed(seed)
    start_ts = _now()
    run_id = f"train_{start_ts.strftime('%Y%m%d_%H%M%S')}_{seed}".replace(":", "")
    run_dir = runs_root / start_ts.strftime("%Y%m%d") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    sim_state: Dict[str, object] = {
        "cash_usd": 10_000.0,
        "risk_state": {
            "mode": "NORMAL",
            "equity": 10_000.0,
            "start_equity": 10_000.0,
            "peak_equity": 10_000.0,
        },
    }

    equity_rows: List[Dict[str, object]] = []
    rejects: Counter = Counter()
    trade_count = 0
    first_ts: str | None = None
    last_ts: str | None = None
    stop_reason = "budget_exhausted"

    kill_cfg: Dict[str, object] = {}
    config_path = ROOT / "config.yaml"
    if config_path.exists():
        try:
            kill_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            kill_cfg = {}

    step_limit = int(args.max_steps)
    trade_limit = int(args.max_trades)
    max_runtime = float(args.max_runtime_seconds)
    log_limit = float(args.max_log_mb)

    start_monotonic = time.monotonic()
    for step_no, row in enumerate(_iter_rows(quotes), start=1):
        now = _now()
        elapsed = time.monotonic() - start_monotonic
        if elapsed >= max_runtime:
            stop_reason = "max_runtime_seconds"
            break
        if step_no > step_limit:
            stop_reason = "max_steps"
            break
        if trade_count >= trade_limit:
            stop_reason = "max_trades"
            break
        if _kill_switch_enabled(kill_cfg) and _kill_switch_path(kill_cfg).expanduser().resolve().exists():
            stop_reason = "kill_switch"
            break
        if _log_size_mb(run_dir) > log_limit:
            stop_reason = "max_log_mb"
            break

        sim_state, emitted = run_step(
            row,
            sim_state,
            {
                "logs_dir": run_dir,
                "momentum_threshold_pct": args.momentum_threshold,
                "verify_no_lookahead": True,
                "policy_version": policy_version,
                "risk_overrides": policy_cfg.get("risk_overrides", {}),
            },
        )

        decision_events = [e for e in emitted if e.get("decision")]
        for event in decision_events:
            decision = str(event.get("decision"))
            reason = str(event.get("reason") or "")
            if decision == "ALLOW":
                trade_count += 1
            elif reason:
                rejects[reason] += 1

        risk_state = sim_state.get("risk_state", {}) or {}
        equity = float(risk_state.get("equity", sim_state.get("cash_usd", 0.0)))
        cash = float(sim_state.get("cash_usd", 0.0))
        peak = float(risk_state.get("peak_equity", equity)) or equity
        drawdown_pct = ((peak - equity) / peak * 100.0) if peak else 0.0
        ts_raw = row.get("ts_utc") or row.get("ts")
        ts = str(ts_raw) if ts_raw else now.isoformat()
        if first_ts is None:
            first_ts = ts
        last_ts = ts
        equity_rows.append(
            {
                "ts_utc": ts,
                "equity_usd": round(equity, 2),
                "cash_usd": round(cash, 2),
                "drawdown_pct": round(drawdown_pct, 4),
                "step": step_no,
                "policy_version": policy_version,
                "mode": risk_state.get("mode", "UNKNOWN"),
            }
        )

    if stop_reason == "budget_exhausted":
        stop_reason = "input_exhausted"

    end_ts = _now()

    outputs = {
        "equity_curve.csv": run_dir / "equity_curve.csv",
        "orders_sim.jsonl": run_dir / "orders_sim.jsonl",
        "summary.md": run_dir / "summary.md",
    }
    _write_equity_csv(outputs["equity_curve.csv"], equity_rows)

    meta = {
        "run_id": run_id,
        "seed": seed,
        "input": str(input_path),
        "policy_version": policy_version,
        "start_ts": start_ts.isoformat(),
        "end_ts": end_ts.isoformat(),
        "first_row_ts": first_ts,
        "last_row_ts": last_ts,
        "params": {
            "max_runtime_seconds": max_runtime,
            "max_steps": step_limit,
            "max_trades": trade_limit,
            "max_log_mb": log_limit,
            "momentum_threshold": args.momentum_threshold,
            "verify_no_lookahead": True,
        },
        "stop_reason": stop_reason,
        "steps_completed": len(equity_rows),
        "trades": trade_count,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_body = _summary_md(
        run_id=run_id,
        policy_version=policy_version,
        equity_rows=equity_rows,
        trade_count=trade_count,
        rejects=rejects,
        stop_reason=stop_reason,
        outputs={k: v.name for k, v in outputs.items()},
    )
    outputs["summary.md"].write_text(summary_body, encoding="utf-8")

    print(f"RUN_DIR={run_dir}")
    print(f"STOP_REASON={stop_reason}")
    print(f"SUMMARY_PATH={outputs['summary.md']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
