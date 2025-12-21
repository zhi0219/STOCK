from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = ROOT / "Logs" / "train_runs"
OUTPUT_ROOT = ROOT / "Logs" / "train_service"
OUTPUT_INDEX = OUTPUT_ROOT / "progress_index.json"


@dataclass
class RunProgress:
    run_dir: str
    summary_path: Optional[str]
    equity_curve: Optional[str]
    final_equity: Optional[float]
    max_drawdown: Optional[float]
    turnover: Optional[float]
    reject_count: Optional[float]
    gate_triggers: Optional[str]
    timestamp: str
    holdings_path: Optional[str]
    cash_usd: Optional[float]


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _extract_first_float(text: str) -> Optional[float]:
    match = re.search(r"[-+]?[0-9]*\.?[0-9]+", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _calc_drawdown(equities: Sequence[float]) -> float:
    peak = equities[0] if equities else 0.0
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        if peak <= 0:
            continue
        max_dd = max(max_dd, (peak - eq) / peak)
    return max_dd


def _read_equity_curve(path: Path) -> tuple[list[float], Optional[float]]:
    if not path.exists():
        return [], None
    equities: list[float] = []
    last_cash: Optional[float] = None
    try:
        with path.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    equities.append(float(row.get("equity_usd") or 0.0))
                except Exception:
                    continue
                try:
                    last_cash = float(row.get("cash_usd")) if row.get("cash_usd") else last_cash
                except Exception:
                    pass
    except Exception:
        return [], None
    return equities, last_cash


def _parse_summary_metrics(summary_path: Path) -> dict[str, Optional[float | str]]:
    metrics: dict[str, Optional[float | str]] = {
        "max_drawdown": None,
        "turnover": None,
        "reject_count": None,
        "gate_triggers": None,
        "final_equity": None,
    }
    if not summary_path.exists():
        return metrics
    try:
        lines = summary_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return metrics

    for line in lines:
        if line.startswith("Max drawdown:"):
            val = _extract_first_float(line)
            metrics["max_drawdown"] = val
        elif line.startswith("Turnover:") or line.startswith("Portfolio turnover:"):
            metrics["turnover"] = _extract_first_float(line)
        elif line.startswith("Reject count:"):
            metrics["reject_count"] = _extract_first_float(line)
        elif line.startswith("Gates triggered:"):
            metrics["gate_triggers"] = line.split(":", 1)[1].strip()
        elif line.startswith("Net value change:"):
            metrics["final_equity"] = _extract_first_float(line)
    return metrics


def _safe_timestamp_from_path(path: Path) -> float:
    parts = re.findall(r"(\d{8})", str(path))
    if parts:
        try:
            dt = datetime.strptime(parts[-1], "%Y%m%d")
            return dt.timestamp()
        except Exception:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return time.time()


def _ensure_holdings(run_dir: Path, equity_curve: Path, cash_usd: Optional[float]) -> Optional[Path]:
    snapshot_path = run_dir / "holdings_snapshot.json"
    if snapshot_path.exists():
        return snapshot_path
    equities, last_cash = _read_equity_curve(equity_curve)
    if not equities and cash_usd is None:
        return None
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "cash_usd": cash_usd if cash_usd is not None else last_cash,
        "holdings": [],
        "notes": "SIM-only synthetic snapshot based on equity curve",
    }
    try:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return snapshot_path
    except Exception:
        return None


def _build_run_entry(run_dir: Path) -> RunProgress:
    summary_path = run_dir / "summary.md"
    equity_curve = run_dir / "equity_curve.csv"
    equities, last_cash = _read_equity_curve(equity_curve)
    summary_metrics = _parse_summary_metrics(summary_path)

    final_equity = equities[-1] if equities else summary_metrics.get("final_equity")  # type: ignore[arg-type]
    max_drawdown = (
        _calc_drawdown(equities) * 100 if equities else summary_metrics.get("max_drawdown")
    )
    turnover = summary_metrics.get("turnover")
    reject_count = summary_metrics.get("reject_count")
    gate_triggers = summary_metrics.get("gate_triggers")

    holdings_path: Optional[Path] = None
    if equity_curve.exists():
        holdings_path = _ensure_holdings(run_dir, equity_curve, last_cash)

    ts = _safe_timestamp_from_path(run_dir)
    ts_text = datetime.fromtimestamp(ts).isoformat()

    return RunProgress(
        run_dir=str(run_dir),
        summary_path=str(summary_path) if summary_path.exists() else None,
        equity_curve=str(equity_curve) if equity_curve.exists() else None,
        final_equity=final_equity if isinstance(final_equity, (int, float)) else None,
        max_drawdown=max_drawdown if isinstance(max_drawdown, (int, float)) else None,
        turnover=turnover if isinstance(turnover, (int, float)) else None,
        reject_count=reject_count if isinstance(reject_count, (int, float)) else None,
        gate_triggers=str(gate_triggers) if gate_triggers is not None else None,
        timestamp=ts_text,
        holdings_path=str(holdings_path) if holdings_path else None,
        cash_usd=last_cash,
    )


def _iter_run_dirs(runs_root: Path) -> Iterable[Path]:
    if not runs_root.exists():
        return []
    run_dirs: List[Path] = []
    for day_dir in runs_root.glob("*"):
        if not day_dir.is_dir():
            continue
        for run_dir in day_dir.iterdir():
            if run_dir.is_dir():
                run_dirs.append(run_dir)
    return sorted(run_dirs)


def build_progress_index(
    runs_root: Path = RUNS_ROOT,
    output_index: Path = OUTPUT_INDEX,
    emit_markers: bool = True,
) -> dict:
    runs_root = Path(runs_root)
    output_index = Path(output_index)
    allowed_root = OUTPUT_ROOT.resolve()
    resolved_output = output_index.resolve()
    try:
        resolved_output.parent.relative_to(allowed_root)
    except ValueError:
        raise ValueError(f"Output must stay within {allowed_root}, got {resolved_output}")

    if emit_markers:
        print("PROGRESS_INDEX_START")
    run_entries: List[RunProgress] = []
    for run_dir in _iter_run_dirs(runs_root):
        try:
            run_entries.append(_build_run_entry(run_dir))
        except Exception:
            continue

    runs_payload = [asdict(run) for run in run_entries]
    latest_run = max(run_entries, key=lambda r: r.timestamp, default=None)
    best_equity = max(
        [r for r in run_entries if r.final_equity is not None],
        key=lambda r: float(r.final_equity or 0.0),
        default=None,
    )
    best_drawdown = min(
        [r for r in run_entries if r.max_drawdown is not None],
        key=lambda r: float(r.max_drawdown or 0.0),
        default=None,
    )

    status = "PASS" if run_entries else "DEGRADED"
    summary_line = "|".join(
        [
            "PROGRESS_INDEX_SUMMARY",
            f"status={status}",
            f"runs={len(run_entries)}",
            f"latest_run={latest_run.run_dir if latest_run else '(none)'}",
            f"best_equity={best_equity.final_equity if best_equity else 'n/a'}",
            f"best_dd={best_drawdown.max_drawdown if best_drawdown else 'n/a'}",
        ]
    )
    if emit_markers:
        print(summary_line)

    index_payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "status": status,
        "runs": runs_payload,
        "latest_run": asdict(latest_run) if latest_run else None,
        "best_equity_run": asdict(best_equity) if best_equity else None,
        "best_drawdown_run": asdict(best_drawdown) if best_drawdown else None,
    }
    _atomic_write_json(output_index, index_payload)
    if emit_markers:
        print("PROGRESS_INDEX_END")
    return index_payload


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build SIM-only progress index")
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT, help="Root of train runs")
    parser.add_argument("--output", type=Path, default=OUTPUT_INDEX, help="Output JSON path")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        build_progress_index(args.runs_root, args.output)
    except Exception as exc:
        print(f"PROGRESS_INDEX_SUMMARY|status=FAIL|error={exc}")
        print("PROGRESS_INDEX_END")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
