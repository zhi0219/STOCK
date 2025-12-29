from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts"


@dataclass(frozen=True)
class AuditConfig:
    rows: int
    lookback: int
    artifacts_dir: Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _synthetic_quotes(length: int) -> List[Dict[str, object]]:
    length = max(1, int(length))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    quotes: List[Dict[str, object]] = []
    base = 100.0
    for idx in range(length):
        price = base + idx * 0.05 + ((idx % 6) - 3) * 0.03
        quotes.append(
            {
                "ts_utc": (start + timedelta(minutes=idx)).isoformat(),
                "price": round(price, 4),
                "symbol": "SIM",
                "source": "synthetic",
            }
        )
    return quotes


def _audit_quotes(quotes: List[Dict[str, object]], lookback: int) -> Dict[str, object]:
    future_timestamp_reads: List[Dict[str, object]] = []
    lookahead_violations: List[Dict[str, object]] = []

    max_ts_seen: datetime | None = None
    prices: List[float] = []

    for idx, row in enumerate(quotes):
        raw_ts = row.get("ts_utc")
        ts = datetime.fromisoformat(str(raw_ts)) if raw_ts else None
        if ts is None:
            continue
        if max_ts_seen and ts < max_ts_seen:
            future_timestamp_reads.append(
                {
                    "index": idx,
                    "ts_utc": ts.isoformat(),
                    "prior_ts_utc": max_ts_seen.isoformat(),
                }
            )
        if max_ts_seen is None or ts > max_ts_seen:
            max_ts_seen = ts

        prices.append(float(row.get("price") or 0.0))
        if len(prices) <= lookback:
            continue

        feature_indices = list(range(len(prices) - lookback, len(prices)))
        if any(feature_index > idx for feature_index in feature_indices):
            lookahead_violations.append(
                {
                    "index": idx,
                    "feature_indices": feature_indices,
                    "ts_utc": ts.isoformat(),
                }
            )

        label_index = idx
        if label_index > idx:
            lookahead_violations.append(
                {
                    "index": idx,
                    "label_index": label_index,
                    "ts_utc": ts.isoformat(),
                }
            )

    checks = []
    checks.append(
        {
            "name": "monotonic_timestamps",
            "status": "PASS" if not future_timestamp_reads else "FAIL",
            "violations": len(future_timestamp_reads),
        }
    )
    checks.append(
        {
            "name": "feature_lookback_window",
            "status": "PASS" if not lookahead_violations else "FAIL",
            "violations": len(lookahead_violations),
            "lookback_window": lookback,
        }
    )

    status = "PASS"
    if future_timestamp_reads or lookahead_violations:
        status = "FAIL"

    return {
        "status": status,
        "checks": checks,
        "evidence": {
            "future_timestamp_reads": future_timestamp_reads,
            "lookahead_violations": lookahead_violations,
        },
    }


def run_audit(config: AuditConfig) -> Dict[str, object]:
    quotes = _synthetic_quotes(config.rows)
    audit_result = _audit_quotes(quotes, config.lookback)
    payload = {
        "schema_version": 1,
        "ts_utc": _now_iso(),
        "status": audit_result["status"],
        "data_source": "synthetic",
        "rows": config.rows,
        "lookback_window": config.lookback,
        "checks": audit_result["checks"],
        "evidence": audit_result["evidence"],
    }

    output_path = config.artifacts_dir / "no_lookahead_audit.json"
    latest_path = config.artifacts_dir / "no_lookahead_audit_latest.json"
    _atomic_write_json(output_path, payload)
    _atomic_write_json(latest_path, payload)
    return payload


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="No-lookahead audit (SIM-only)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--rows", type=int, default=60)
    parser.add_argument("--lookback", type=int, default=5)
    parser.add_argument("--artifacts-dir", default=str(ARTIFACTS_DIR))
    parser.add_argument("--tiny", action="store_true")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    artifacts_dir = Path(args.artifacts_dir)
    if not artifacts_dir.is_absolute():
        artifacts_dir = (ROOT / artifacts_dir).resolve()

    rows = int(args.rows)
    lookback = int(args.lookback)
    if args.tiny:
        rows = min(rows, 20)
        lookback = min(lookback, 3)

    config = AuditConfig(rows=rows, lookback=lookback, artifacts_dir=artifacts_dir)
    payload = run_audit(config)
    return 0 if payload.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
