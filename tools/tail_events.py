from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}
    return {}


def get_logs_dir(cfg: dict) -> Path:
    logging_cfg = cfg.get("logging", {}) or {}
    return ROOT / str(logging_cfg.get("log_dir", "./Logs"))


def latest_events_file(logs_dir: Path) -> Optional[Path]:
    candidates = sorted(logs_dir.glob("events_*.jsonl"))
    if candidates:
        return candidates[-1]

    fallback = logs_dir / "events.jsonl"
    if fallback.exists():
        return fallback
    return None


def iter_events(path: Path) -> Iterable[tuple[int, str]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            for idx, line in enumerate(f, start=1):
                yield idx, line.rstrip("\n")
    except FileNotFoundError:
        return
    except Exception as e:
        print(f"WARN: failed to read {path}: {e}", file=sys.stderr)
        return


def filter_events(
    path: Path,
    *,
    limit: int,
    symbol: Optional[str],
    event_type: Optional[str],
    severity: Optional[str],
) -> list[dict]:
    symbol_upper = symbol.upper() if symbol else None
    event_type = event_type.upper() if event_type else None
    severity = severity.lower() if severity else None

    results: deque[dict] = deque(maxlen=limit)

    for lineno, raw in iter_events(path):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except Exception as e:
            print(f"WARN: skip bad line {path}#{lineno}: {e}", file=sys.stderr)
            continue

        if symbol_upper and str(payload.get("symbol", "")).upper() != symbol_upper:
            continue
        if event_type and str(payload.get("event_type", "")).upper() != event_type:
            continue
        if severity and str(payload.get("severity", "")).lower() != severity:
            continue

        results.append(payload)

    return list(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tail recent events with optional filters.")
    parser.add_argument("--limit", type=int, default=20, help="Number of rows to display (default: 20)")
    parser.add_argument("--symbol", help="Filter by symbol (case-insensitive)")
    parser.add_argument("--type", dest="event_type", help="Filter by event_type (case-insensitive)")
    parser.add_argument("--severity", help="Filter by severity (case-insensitive)")
    args = parser.parse_args()

    cfg = load_config()
    logs_dir = get_logs_dir(cfg)

    events_path = latest_events_file(logs_dir)
    if not events_path:
        print(f"No events file found in {logs_dir}")
        sys.exit(0)

    rows = filter_events(
        events_path,
        limit=max(1, args.limit),
        symbol=args.symbol,
        event_type=args.event_type,
        severity=args.severity,
    )

    if not rows:
        print(f"No matching events found in {events_path}")
        return

    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()

