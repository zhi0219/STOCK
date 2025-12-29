from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.fs_atomic import atomic_write_json
from tools.paths import logs_dir, repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_QUOTES = ROOT / "Data" / "quotes.csv"


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _load_quotes(path: Path, limit: int | None) -> tuple[list[dict[str, Any]], str]:
    quotes: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                quotes.append({"ts_utc": row.get("ts_utc"), "price": row.get("price")})
                if limit is not None and len(quotes) >= limit:
                    break
    if quotes:
        return quotes, "quotes.csv"

    synthetic: list[dict[str, Any]] = []
    base = 100.0
    for idx in range(120):
        synthetic.append({"ts_utc": None, "price": f"{base + idx * 0.1:.2f}"})
    return synthetic, "synthetic_fallback"


def _audit_monotonic(quotes: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    last_ts: datetime | None = None
    for idx, row in enumerate(quotes):
        ts_value = row.get("ts_utc")
        if not ts_value:
            continue
        ts = _parse_ts(str(ts_value))
        if ts is None:
            issues.append(f"invalid_ts:{idx}")
            continue
        if last_ts and ts < last_ts:
            issues.append(f"timestamp_regressed:{idx}")
        last_ts = ts
    return issues


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="No-lookahead audit (SIM-only)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default=str(DEFAULT_QUOTES), help="Quotes CSV input")
    parser.add_argument("--output-dir", default=str(logs_dir() / "runtime" / "no_lookahead"))
    parser.add_argument("--latest-dir", default=None, help="Directory for _latest pointer")
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
    issues = _audit_monotonic(quotes)
    status = "PASS" if not issues else "FAIL"

    payload = {
        "schema_version": 1,
        "ts_utc": _now_ts(),
        "status": status,
        "issues": issues,
        "data_source": data_source,
        "checks": {
            "timestamps_present": any(row.get("ts_utc") for row in quotes),
            "rows_checked": len(quotes),
        },
        "input_path": to_repo_relative(input_path) if input_path.exists() else None,
        "result_path": to_repo_relative(output_dir / "no_lookahead_audit.json"),
    }

    result_path = output_dir / "no_lookahead_audit.json"
    latest_path = latest_dir / "no_lookahead_audit_latest.json"
    atomic_write_json(result_path, payload)
    atomic_write_json(latest_path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
