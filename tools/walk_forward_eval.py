from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.fs_atomic import atomic_write_json, atomic_write_text
from tools.paths import logs_dir, repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_QUOTES = ROOT / "Data" / "quotes.csv"
DEFAULT_WINDOWS = 3
DEFAULT_MIN_WINDOW_SIZE = 40
DEFAULT_MAX_DRAWDOWN_PCT = 5.0
DEFAULT_MIN_RETURN_PCT = 0.0
DEFAULT_WINDOW_PASSES_REQUIRED = 2


@dataclass(frozen=True)
class Quote:
    price: float
    ts_utc: str | None = None


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_quotes(path: Path, limit: int | None = None) -> tuple[list[Quote], str]:
    quotes: list[Quote] = []
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw_price = row.get("price")
                if raw_price in (None, ""):
                    continue
                try:
                    price_val = float(raw_price)
                except ValueError:
                    continue
                ts_utc = row.get("ts_utc") or None
                quotes.append(Quote(price=price_val, ts_utc=ts_utc))
                if limit is not None and len(quotes) >= limit:
                    break
    if quotes:
        return quotes, "quotes.csv"

    length = max(DEFAULT_MIN_WINDOW_SIZE * DEFAULT_WINDOWS, 120)
    synthetic: list[Quote] = []
    base = 100.0
    for idx in range(length):
        seasonal = (idx % 10) - 5
        price = base + idx * 0.08 + seasonal * 0.15
        synthetic.append(Quote(price=round(price, 4), ts_utc=None))
    return synthetic, "synthetic_fallback"


def _max_drawdown(prices: Iterable[float]) -> float:
    peak = None
    max_drawdown = 0.0
    for price in prices:
        if peak is None or price > peak:
            peak = price
        if peak and peak > 0:
            drawdown = (peak - price) / peak * 100.0
            if drawdown > max_drawdown:
                max_drawdown = drawdown
    return max_drawdown


def _window_bounds(total: int, window_count: int, min_size: int) -> list[tuple[int, int]]:
    if total <= 0:
        return []
    window_count = max(1, int(window_count))
    min_size = max(1, int(min_size))
    size = max(min_size, total // window_count)
    bounds: list[tuple[int, int]] = []
    start = 0
    while start < total:
        end = min(total, start + size)
        bounds.append((start, end))
        start = end
    return bounds


def _build_windows(
    quotes: list[Quote],
    bounds: list[tuple[int, int]],
    max_drawdown_pct: float,
    min_return_pct: float,
    run_id: str,
    data_source: str,
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for idx, (start, end) in enumerate(bounds, start=1):
        subset = quotes[start:end]
        if not subset:
            continue
        start_price = subset[0].price
        end_price = subset[-1].price
        return_pct = 0.0
        if start_price:
            return_pct = (end_price - start_price) / start_price * 100.0
        drawdown_pct = _max_drawdown([q.price for q in subset])
        score = round(return_pct - drawdown_pct, 4)
        reasons: list[str] = []
        status = "PASS"
        if return_pct < min_return_pct:
            status = "FAIL"
            reasons.append(f"return_below:{min_return_pct:.2f}pct")
        if drawdown_pct > max_drawdown_pct:
            status = "FAIL"
            reasons.append(f"drawdown_above:{max_drawdown_pct:.2f}pct")
        windows.append(
            {
                "schema_version": 1,
                "ts_utc": _now_ts(),
                "run_id": run_id,
                "window_id": idx,
                "start_index": start,
                "end_index": end - 1,
                "status": status,
                "reasons": reasons,
                "metrics": {
                    "return_pct": round(return_pct, 4),
                    "max_drawdown_pct": round(drawdown_pct, 4),
                    "score": score,
                    "start_price": start_price,
                    "end_price": end_price,
                },
                "ts_start": subset[0].ts_utc,
                "ts_end": subset[-1].ts_utc,
                "data_source": data_source,
            }
        )
    return windows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    atomic_write_text(path, payload)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walk-forward evaluation (SIM-only)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default=str(DEFAULT_QUOTES), help="Quotes CSV input")
    parser.add_argument("--output-dir", default=str(logs_dir() / "runtime" / "walk_forward"))
    parser.add_argument("--latest-dir", default=None, help="Directory for _latest pointers")
    parser.add_argument("--window-count", type=int, default=DEFAULT_WINDOWS)
    parser.add_argument("--window-passes-required", type=int, default=DEFAULT_WINDOW_PASSES_REQUIRED)
    parser.add_argument("--min-window-size", type=int, default=DEFAULT_MIN_WINDOW_SIZE)
    parser.add_argument("--max-drawdown-pct", type=float, default=DEFAULT_MAX_DRAWDOWN_PCT)
    parser.add_argument("--min-return-pct", type=float, default=DEFAULT_MIN_RETURN_PCT)
    parser.add_argument("--limit", type=int, default=None, help="Max rows to read")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    latest_dir = Path(args.latest_dir) if args.latest_dir else output_dir / "_latest"
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)

    quotes, data_source = _load_quotes(input_path, limit=args.limit)
    run_id = f"walk_forward_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    bounds = _window_bounds(len(quotes), args.window_count, args.min_window_size)
    windows = _build_windows(
        quotes,
        bounds,
        max_drawdown_pct=args.max_drawdown_pct,
        min_return_pct=args.min_return_pct,
        run_id=run_id,
        data_source=data_source,
    )
    window_passes = sum(1 for window in windows if window.get("status") == "PASS")
    window_required = max(1, int(args.window_passes_required))
    status = "PASS" if window_passes >= window_required and len(windows) >= window_required else "FAIL"
    reasons: list[str] = []
    if len(windows) < window_required:
        reasons.append("insufficient_window_count")
    if status != "PASS":
        reasons.append("insufficient_window_passes")

    result_path = output_dir / "walk_forward_result.json"
    windows_path = output_dir / "walk_forward_windows.jsonl"
    latest_result_path = latest_dir / "walk_forward_result_latest.json"
    latest_windows_path = latest_dir / "walk_forward_windows_latest.jsonl"

    result_payload = {
        "schema_version": 1,
        "ts_utc": _now_ts(),
        "run_id": run_id,
        "status": status,
        "window_count": len(windows),
        "window_passes": window_passes,
        "window_passes_required": window_required if windows else 0,
        "reasons": reasons,
        "data_source": data_source,
        "input_path": to_repo_relative(input_path) if input_path.exists() else None,
        "result_path": to_repo_relative(result_path),
        "windows_path": to_repo_relative(windows_path),
    }

    atomic_write_json(result_path, result_payload)
    _write_jsonl(windows_path, windows)
    atomic_write_json(latest_result_path, result_payload)
    _write_jsonl(latest_windows_path, windows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
