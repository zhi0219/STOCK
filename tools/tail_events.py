from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> Dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_logs_dir(cfg: Dict[str, Any]) -> Path:
    logging_cfg = cfg.get("logging", {}) or {}
    return ROOT / str(logging_cfg.get("log_dir", "./Logs"))


def find_latest_events_file(logs_dir: Path) -> Optional[Path]:
    candidates = sorted(logs_dir.glob("events_*.jsonl"))
    return candidates[-1] if candidates else None


def iter_events(path: Path) -> Iterable[Dict[str, Any]]:
    bad_lines = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                bad_lines += 1
    if bad_lines:
        print(f"[WARN] skipped {bad_lines} bad line(s) in {path}", file=sys.stderr)


def filter_events(
    events: Iterable[Dict[str, Any]],
    *,
    symbol: Optional[str],
    event_type: Optional[str],
    since_minutes: Optional[float],
) -> Iterable[Dict[str, Any]]:
    since_dt: Optional[datetime] = None
    if since_minutes is not None:
        since_dt = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    for ev in events:
        if symbol and str(ev.get("symbol", "")).upper() != symbol:
            continue
        if event_type and str(ev.get("event_type", "")).upper() != event_type:
            continue
        if since_dt is not None:
            ts_utc = ev.get("ts_utc")
            try:
                dt = datetime.fromisoformat(str(ts_utc))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt < since_dt:
                continue
        yield ev


def main() -> None:
    parser = argparse.ArgumentParser(description="Tail latest events jsonl")
    parser.add_argument("--symbol", help="filter by symbol", dest="symbol")
    parser.add_argument("--type", help="filter by event_type", dest="event_type")
    parser.add_argument("--since-minutes", type=float, help="only events within N minutes")
    parser.add_argument("--tail", type=int, default=20, help="number of lines from the end to show")
    args = parser.parse_args()

    cfg = load_config()
    logs_dir = get_logs_dir(cfg)
    latest = find_latest_events_file(logs_dir)
    if latest is None:
        print(f"No events file found in {logs_dir}", file=sys.stderr)
        sys.exit(1)

    symbol = args.symbol.upper() if args.symbol else None
    event_type = args.event_type.upper() if args.event_type else None
    events = list(filter_events(iter_events(latest), symbol=symbol, event_type=event_type, since_minutes=args.since_minutes))

    if not events:
        print(f"No events matched filters in {latest}")
        return

    for ev in events[-args.tail :]:
        print(json.dumps(ev, ensure_ascii=False))


if __name__ == "__main__":
    main()
