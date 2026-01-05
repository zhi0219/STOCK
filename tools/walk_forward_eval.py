from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_DATA_PATH = ROOT / "Data" / "quotes.csv"
FIXTURE_DATA_PATH = ROOT / "fixtures" / "walk_forward" / "ohlcv.csv"
DEFAULT_TIMEZONE = "America/New_York"


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class WindowSpec:
    index: int
    train_start: int
    train_end: int
    gap_start: int
    gap_end: int
    test_start: int
    test_end: int


StrategyFn = Callable[[Sequence[Bar]], float]


def _resolve_data_path(raw_path: str | None) -> Path:
    if raw_path:
        path = Path(raw_path)
    else:
        path = DEFAULT_DATA_PATH if DEFAULT_DATA_PATH.exists() else FIXTURE_DATA_PATH
    if not path.is_absolute():
        path = ROOT / path
    path = path.expanduser().resolve()
    if path.is_dir():
        candidates = sorted(path.glob("*.csv"))
        if not candidates:
            raise FileNotFoundError(f"No CSV files found in {path}")
        path = candidates[0]
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    return path


def _parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_timestamp(raw: str, tz: ZoneInfo) -> datetime:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _load_bars(path: Path, tz: ZoneInfo) -> list[Bar]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        return []
    columns = {name.lower(): name for name in rows[0].keys() if name}
    ts_key = columns.get("timestamp") or columns.get("ts") or columns.get("datetime")
    open_key = columns.get("open")
    high_key = columns.get("high")
    low_key = columns.get("low")
    close_key = columns.get("close") or columns.get("price")
    volume_key = columns.get("volume") or columns.get("vol")
    if not ts_key or not close_key:
        raise ValueError("Input CSV must include timestamp and close columns")

    bars: list[Bar] = []
    for row in rows:
        raw_ts = row.get(ts_key) or ""
        if not raw_ts:
            continue
        ts = _parse_timestamp(raw_ts, tz)
        bars.append(
            Bar(
                timestamp=ts,
                open=_parse_float(row.get(open_key)),
                high=_parse_float(row.get(high_key)),
                low=_parse_float(row.get(low_key)),
                close=_parse_float(row.get(close_key)),
                volume=_parse_float(row.get(volume_key)),
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars


def _return_pct(prices: Sequence[Bar], position: float) -> float:
    if len(prices) < 2:
        return 0.0
    start_price = prices[0].close
    end_price = prices[-1].close
    if start_price == 0:
        return 0.0
    return round((end_price - start_price) / start_price * 100.0 * position, 4)


def _simple_momentum(train: Sequence[Bar]) -> float:
    if len(train) < 2:
        return 0.0
    return 1.0 if train[-1].close > train[0].close else 0.0


def _placeholder_policy(train: Sequence[Bar]) -> float:
    return _simple_momentum(train)


BASELINE_POLICIES: dict[str, StrategyFn] = {
    "DoNothing": lambda train: 0.0,
    "BuyHold": lambda train: 1.0,
    "SimpleMomentum": _simple_momentum,
}

STRATEGY_POLICIES: dict[str, StrategyFn] = {
    "placeholder": _placeholder_policy,
}


def build_windows(total: int, train_size: int, gap_size: int, test_size: int, step_size: int) -> list[WindowSpec]:
    if total <= 0:
        return []
    train_size = max(1, int(train_size))
    gap_size = max(0, int(gap_size))
    test_size = max(1, int(test_size))
    step_size = max(1, int(step_size))

    windows: list[WindowSpec] = []
    start = 0
    idx = 1
    while start + train_size + gap_size + test_size <= total:
        train_start = start
        train_end = start + train_size
        gap_start = train_end
        gap_end = gap_start + gap_size
        test_start = gap_end
        test_end = test_start + test_size
        windows.append(
            WindowSpec(
                index=idx,
                train_start=train_start,
                train_end=train_end,
                gap_start=gap_start,
                gap_end=gap_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        idx += 1
        start += step_size
    return windows


def evaluate_walk_forward(
    bars: Sequence[Bar],
    window_specs: Sequence[WindowSpec],
    strategy_name: str,
) -> dict[str, object]:
    if strategy_name not in STRATEGY_POLICIES:
        raise ValueError(f"Unknown strategy policy: {strategy_name}")
    strategy_fn = STRATEGY_POLICIES[strategy_name]

    windows: list[dict[str, object]] = []
    for spec in window_specs:
        train = bars[spec.train_start : spec.train_end]
        gap = bars[spec.gap_start : spec.gap_end]
        test = bars[spec.test_start : spec.test_end]
        strategy_signal = strategy_fn(train)
        strategy_return = _return_pct(test, strategy_signal)
        baseline_returns = {
            name: _return_pct(test, policy(train)) for name, policy in BASELINE_POLICIES.items()
        }
        comparison = {
            name: round(strategy_return - baseline_return, 4)
            for name, baseline_return in baseline_returns.items()
        }
        windows.append(
            {
                "window_index": spec.index,
                "train_range": (spec.train_start, spec.train_end - 1),
                "gap_range": (spec.gap_start, spec.gap_end - 1) if spec.gap_end > spec.gap_start else None,
                "test_range": (spec.test_start, spec.test_end - 1),
                "train_start": bars[spec.train_start].timestamp.isoformat(),
                "train_end": bars[spec.train_end - 1].timestamp.isoformat(),
                "test_start": bars[spec.test_start].timestamp.isoformat(),
                "test_end": bars[spec.test_end - 1].timestamp.isoformat(),
                "strategy": {
                    "name": strategy_name,
                    "signal": strategy_signal,
                    "return_pct": strategy_return,
                },
                "baselines": baseline_returns,
                "comparison": comparison,
                "gap_size": len(gap),
            }
        )
    strategy_returns = [window["strategy"]["return_pct"] for window in windows]
    baseline_averages = {
        name: round(sum(window["baselines"][name] for window in windows) / len(windows), 4)
        if windows
        else 0.0
        for name in BASELINE_POLICIES
    }
    summary = {
        "window_count": len(windows),
        "strategy_average_return_pct": round(sum(strategy_returns) / len(strategy_returns), 4) if windows else 0.0,
        "baseline_average_returns_pct": baseline_averages,
    }
    return {
        "strategy": strategy_name,
        "baselines": list(BASELINE_POLICIES.keys()),
        "windows": windows,
        "summary": summary,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic walk-forward evaluation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default=None, help="Input OHLCV CSV path")
    parser.add_argument("--train-size", type=int, default=5, help="Training window size (bars)")
    parser.add_argument("--gap-size", type=int, default=2, help="Embargo/gap size (bars)")
    parser.add_argument("--test-size", type=int, default=4, help="Test window size (bars)")
    parser.add_argument("--step-size", type=int, default=4, help="Rolling step size (bars)")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="Timezone for timestamps")
    parser.add_argument("--strategy", default="placeholder", help="Strategy policy name")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tz = ZoneInfo(args.timezone)
    data_path = _resolve_data_path(args.input)
    bars = _load_bars(data_path, tz)
    window_specs = build_windows(
        len(bars),
        train_size=args.train_size,
        gap_size=args.gap_size,
        test_size=args.test_size,
        step_size=args.step_size,
    )
    report = evaluate_walk_forward(bars, window_specs, args.strategy)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input_path": to_repo_relative(data_path),
        "timezone": args.timezone,
        "window_config": {
            "train_size": args.train_size,
            "gap_size": args.gap_size,
            "test_size": args.test_size,
            "step_size": args.step_size,
        },
        **report,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
