from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sim_autopilot import run_step

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "Data" / "quotes.csv"
DEFAULT_LOGS = ROOT / "Logs"


class ReplayError(Exception):
    pass


def _iter_rows(path: Path) -> Iterable[Tuple[int, Dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader, start=1):
            yield idx, row


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None


def _write_equity_curve(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _write_portfolio(path: Path, state: Dict[str, object], step: int, ts_utc: str) -> None:
    body = {
        "ts_utc": ts_utc,
        "step": step,
        "cash_usd": state.get("cash_usd", 0.0),
        "positions": state.get("positions", {}),
        "avg_cost": state.get("avg_cost", {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _build_snapshot(row: Dict[str, str]) -> Dict[str, object]:
    snapshot: Dict[str, object] = {k: v for k, v in row.items() if v not in {None, ""}}
    try:
        snapshot["price"] = float(snapshot.get("price") or 0.0)
    except Exception:
        snapshot["price"] = 0.0
    return snapshot


def run_replay(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    input_path = input_path.expanduser().resolve()
    if not input_path.exists():
        raise ReplayError(f"Input file not found: {input_path}")

    logs_dir = Path(args.logs_dir)
    if not logs_dir.is_absolute():
        logs_dir = ROOT / logs_dir
    logs_dir = logs_dir.expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)

    eq_path = logs_dir / "equity_curve.jsonl"
    portfolio_path = logs_dir / "portfolio_sim.json"

    max_steps = int(args.max_steps)
    symbol_filter: List[str] | None = None
    if args.symbols:
        symbol_filter = [sym.strip().upper() for sym in args.symbols.split(",") if sym.strip()]
    start_ts = _parse_ts(args.start_ts) if args.start_ts else None
    start_row = int(args.start_row) if args.start_row else None
    sleep_delay = 0.0 if args.speed <= 0 else 1.0 / float(args.speed)

    sim_state: Dict[str, object] = {
        "cash_usd": 10_000.0,
        "risk_state": {"mode": "NORMAL", "equity": 10_000.0, "start_equity": 10_000.0, "peak_equity": 10_000.0},
    }

    steps = 0
    for row_no, row in _iter_rows(input_path):
        if start_row and row_no < start_row:
            continue
        snapshot = _build_snapshot(row)
        ts_obj = _parse_ts(str(snapshot.get("ts_utc") or snapshot.get("ts"))) or datetime.now(timezone.utc)
        if start_ts and ts_obj < start_ts:
            continue
        symbol = str(snapshot.get("symbol") or "-").upper()
        if symbol_filter and symbol not in symbol_filter:
            continue

        sim_state, _ = run_step(
            snapshot,
            sim_state,
            {
                "logs_dir": logs_dir,
                "momentum_threshold_pct": args.threshold,
                "verify_no_lookahead": bool(args.verify_no_lookahead),
            },
        )
        steps += 1

        risk_state = sim_state.get("risk_state", {}) or {}
        equity = float(risk_state.get("equity", sim_state.get("cash_usd", 0.0)))
        cash = float(sim_state.get("cash_usd", 0.0))
        drawdown_pct = float(risk_state.get("drawdown", 0.0)) * 100 if "drawdown" in risk_state else 0.0
        ts_utc = ts_obj.astimezone(timezone.utc)
        ts_et = ts_utc.astimezone(ZoneInfo("US/Eastern"))

        _write_equity_curve(
            eq_path,
            {
                "ts_utc": ts_utc.isoformat(),
                "ts_et": ts_et.isoformat(),
                "equity_usd": round(equity, 2),
                "cash_usd": round(cash, 2),
                "drawdown_pct": round(drawdown_pct, 4),
                "mode": risk_state.get("mode", "UNKNOWN"),
                "step": steps,
            },
        )
        _write_portfolio(portfolio_path, sim_state, steps, ts_utc.isoformat())

        if steps >= max_steps:
            break
        if sleep_delay > 0:
            time.sleep(sleep_delay)

    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sim replay using historical quotes", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Input quotes CSV path")
    parser.add_argument("--max-steps", type=int, default=500, dest="max_steps", help="Maximum steps to run")
    parser.add_argument("--symbols", help="Comma separated symbol filter", default="")
    parser.add_argument("--speed", type=int, default=0, help="Replay speed divisor; 0 disables sleep")
    parser.add_argument("--start-row", type=int, dest="start_row", help="Start from specific CSV row (1-based)")
    parser.add_argument("--start-ts", dest="start_ts", help="Start from specific ts_utc (ISO)")
    parser.add_argument("--logs-dir", default=str(DEFAULT_LOGS), help="Logs directory for outputs")
    parser.add_argument("--threshold", type=float, default=0.5, help="Pct change threshold for generating intents")
    parser.add_argument("--verify-no-lookahead", action="store_true", dest="verify_no_lookahead", help="Enable anti-lookahead assertions")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        return run_replay(args)
    except ReplayError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
