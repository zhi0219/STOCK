from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
DEFAULT_RUNS_ROOT = LOGS_DIR / "train_runs"
DEFAULT_OUTPUT = DEFAULT_RUNS_ROOT / "progress_index.json"

try:
    from tools.wakeup_dashboard import parse_summary_key_fields
except Exception:
    parse_summary_key_fields = None  # type: ignore[assignment]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iter_summaries(runs_root: Path) -> Iterable[Path]:
    if not runs_root.exists():
        return []
    summaries: List[Path] = []
    for summary in runs_root.glob("**/summary.md"):
        try:
            summary.stat().st_mtime
        except OSError:
            continue
        summaries.append(summary)
    summaries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return summaries


def _load_equity_preview(path: Path, limit: int = 120) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rows.append(
                    {
                        "ts": row.get("ts_utc"),
                        "equity": float(row.get("equity_usd") or 0.0),
                        "cash": float(row.get("cash_usd") or 0.0),
                    }
                )
                if len(rows) >= limit:
                    break
    except Exception:
        return []
    return rows


def _ensure_safe_output(runs_root: Path, output_path: Path) -> Path:
    resolved_output = output_path.resolve()
    allowed_roots = [runs_root.resolve(), LOGS_DIR.resolve()]
    if not any(str(resolved_output).startswith(str(root)) for root in allowed_roots):
        raise ValueError(f"output must be under {runs_root} or {LOGS_DIR}")
    return resolved_output


def _aggregate_holdings(orders_path: Path, limit: int = 6) -> list[dict[str, object]]:
    if not orders_path.exists():
        return []
    counts: Dict[str, int] = {}
    try:
        with orders_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                symbol = obj.get("symbol") or obj.get("ticker")
                if not symbol:
                    continue
                counts[str(symbol)] = counts.get(str(symbol), 0) + 1
    except Exception:
        return []
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"symbol": sym, "count": count} for sym, count in top]


def _summary_fields(summary_path: Path) -> dict[str, object]:
    if not parse_summary_key_fields or not summary_path.exists():
        return {}
    parsed = parse_summary_key_fields(summary_path)
    return {
        "stop_reason": parsed.stop_reason,
        "net_change": parsed.net_change,
        "max_drawdown": parsed.max_drawdown,
        "trades_count": parsed.trades_count,
        "reject_reasons_top3": parsed.reject_reasons_top3,
        "turnover": parsed.turnover,
        "reject_count": parsed.reject_count,
        "gates_triggered": parsed.gates_triggered,
        "raw_preview": parsed.raw_preview,
        "warning": parsed.warning,
    }


def build_progress_index(runs_root: Path, max_runs: int = 50) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for summary in _iter_summaries(runs_root):
        run_dir = summary.parent
        entry = {
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "summary_path": str(summary),
            "mtime": datetime.fromtimestamp(summary.stat().st_mtime, tz=timezone.utc).isoformat(),
            "summary": _summary_fields(summary),
        }
        equity_path = run_dir / "equity_curve.csv"
        equity_points = _load_equity_preview(equity_path)
        if equity_points:
            entry["equity_path"] = str(equity_path)
            entry["equity_points"] = equity_points
        orders_path = run_dir / "orders_sim.jsonl"
        holdings = _aggregate_holdings(orders_path)
        if holdings:
            entry["orders_path"] = str(orders_path)
            entry["holdings_preview"] = holdings
        entries.append(entry)
        if len(entries) >= max_runs:
            break

    return {
        "generated_ts": _now(),
        "runs_root": str(runs_root),
        "entries": entries,
    }


def write_progress_index(payload: dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(output_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build progress index for SIM-only runs")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-runs", type=int, default=50)
    args = parser.parse_args(argv)

    status = "PASS"
    message = ""
    payload: dict[str, object] = {"entries": []}
    try:
        safe_output = _ensure_safe_output(args.runs_root, args.output)
        payload = build_progress_index(args.runs_root, max_runs=int(args.max_runs))
        write_progress_index(payload, safe_output)
    except Exception as exc:  # pragma: no cover - fail closed
        status = "FAIL"
        message = str(exc)

    summary = "|".join(
        [
            "PROGRESS_INDEX_SUMMARY",
            f"status={status}",
            f"runs_root={args.runs_root}",
            f"output={args.output}",
            f"runs_found={len(payload['entries']) if status == 'PASS' else 0}",
            f"message={message or 'ok'}",
        ]
    )
    print("PROGRESS_INDEX_START")
    print(summary)
    if status != "PASS":
        print(f"Progress index failed: {message}")
    print("PROGRESS_INDEX_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
