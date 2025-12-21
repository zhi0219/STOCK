from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_yaml_spec = importlib.util.find_spec("yaml")
yaml = importlib.import_module("yaml") if _yaml_spec else None

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stdio_utf8 import configure_stdio_utf8


CONFIG_PATH = ROOT / "config.yaml"
DEFAULT_LIMIT = 50
DEFAULT_SINCE_MINUTES = 60

try:  # pragma: no cover - timezone fallback
    from zoneinfo import ZoneInfo

    ET_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback for older Python
    ET_TZ = timezone(timedelta(hours=-5))


def load_config() -> Dict[str, Any]:
    if yaml is None:
        print("[WARN] PyYAML not available; using empty config", file=sys.stderr)
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_logs_dir(cfg: Dict[str, Any]) -> Path:
    logging_cfg = cfg.get("logging", {}) or {}
    return ROOT / str(logging_cfg.get("log_dir", "./Logs"))


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def find_latest_events_file(logs_dir: Path) -> Optional[Path]:
    candidates = list(logs_dir.glob("events_*.jsonl"))
    if candidates:
        # Sort by mtime then by name to break ties deterministically
        candidates.sort(key=lambda p: (_safe_mtime(p), p.name))
        return candidates[-1]

    legacy = logs_dir / "events.jsonl"
    if legacy.exists():
        return legacy
    return None


def iter_events(path: Path) -> Iterable[Dict[str, Any]]:
    bad_lines = 0
    with path.open("r", encoding="utf-8", errors="ignore") as f:
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


def _parse_ts(ev: Dict[str, Any]) -> Optional[datetime]:
    ts_raw = ev.get("ts_utc") or ev.get("ts_et")
    if not ts_raw:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_raw))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def filter_events(
    events: Iterable[Dict[str, Any]],
    *,
    symbol: Optional[str],
    event_type: Optional[str],
    severity: Optional[str],
    since_minutes: Optional[float],
    contains: Optional[str],
) -> List[Dict[str, Any]]:
    since_dt: Optional[datetime] = None
    if since_minutes is not None:
        since_dt = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    filtered: List[Dict[str, Any]] = []
    for ev in events:
        ev_symbol = str(ev.get("symbol", "")).upper() or None
        ev_type = str(ev.get("event_type", "")).upper() or None
        ev_severity = str(ev.get("severity", "")).lower() or None

        if symbol and ev_symbol != symbol:
            continue
        if event_type and ev_type != event_type:
            continue
        if severity and ev_severity != severity:
            continue

        if since_dt is not None:
            dt = _parse_ts(ev)
            if dt is None or dt < since_dt:
                continue

        if contains:
            message = str(ev.get("message", ""))
            if contains not in message.lower():
                continue

        filtered.append(ev)

    return filtered


def _format_ts(ev: Dict[str, Any]) -> str:
    ts = _parse_ts(ev)
    if not ts:
        return "?"
    try:
        ts = ts.astimezone(timezone.utc)
    except Exception:
        pass
    return ts.isoformat()


def _format_human(ev: Dict[str, Any]) -> str:
    ts = _format_ts(ev)
    event_type = ev.get("event_type", "?")
    symbol = ev.get("symbol") or "-"
    severity = ev.get("severity", "?")
    message = ev.get("message", "").replace("\n", " | ")
    metrics = ev.get("metrics")
    metrics_part = ""
    if isinstance(metrics, dict) and metrics:
        preview = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:4])
        metrics_part = f" | metrics: {preview}"
    return f"[{ts}] {event_type} {symbol} {severity}: {message}{metrics_part}"


def _print_stats(events: List[Dict[str, Any]]) -> None:
    def _show(label: str, counter: Counter) -> None:
        if not counter:
            return
        print(f"\n{label} (count)")
        for key, count in counter.most_common():
            print(f"  {key or '-'}: {count}")

    type_counter: Counter[str] = Counter()
    symbol_counter: Counter[str] = Counter()
    severity_counter: Counter[str] = Counter()
    for ev in events:
        type_counter[ev.get("event_type") or "-"] += 1
        symbol_counter[ev.get("symbol") or "-"] += 1
        severity_counter[ev.get("severity") or "-"] += 1

    print("\n=== Stats ===")
    _show("By event_type", type_counter)
    _show("By symbol", symbol_counter)
    _show("By severity", severity_counter)


def _et_isoformat(dt: datetime) -> str:
    try:
        return dt.astimezone(ET_TZ).isoformat()
    except Exception:
        return dt.isoformat()


def _append_learning_card(
    events: List[Dict[str, Any]],
    *,
    logs_dir: Path,
    source_file: Path,
    args: argparse.Namespace,
) -> None:
    if not events:
        print("[WARN] Cannot write learning card: no events in window", file=sys.stderr)
        return

    data_dir = ROOT / "Data"
    data_dir.mkdir(parents=True, exist_ok=True)
    card_path = data_dir / "learning_cards.md"

    timestamps: List[datetime] = []
    for ev in events:
        dt = _parse_ts(ev)
        if dt:
            timestamps.append(dt)
    if timestamps:
        start = min(timestamps)
        end = max(timestamps)
    else:
        start = end = datetime.now(timezone.utc)

    type_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    for ev in events:
        type_counts[ev.get("event_type") or "-"] += 1
        symbol_counts[ev.get("symbol") or "-"] += 1

    recent = events[-5:]
    highlight_lines = [
        f"- [{_format_ts(ev)}] {ev.get('event_type', '?')} {ev.get('symbol') or '-'}: {ev.get('message', '')}"
        for ev in recent
    ]

    filters_applied = []
    if args.since_minutes is not None:
        filters_applied.append(f"since_minutes={args.since_minutes}")
    if args.limit is not None:
        filters_applied.append(f"limit={args.limit}")
    if args.symbol:
        filters_applied.append(f"symbol={args.symbol}")
    if args.type:
        filters_applied.append(f"type={args.type}")
    if args.severity:
        filters_applied.append(f"severity={args.severity}")
    if args.contains:
        filters_applied.append(f"contains={args.contains}")
    filters_text = ", ".join(filters_applied) if filters_applied else "(none)"

    status_path = logs_dir / "status.json"
    status_note = "status.json not found"
    if status_path.exists():
        try:
            status_data = json.loads(status_path.read_text(encoding="utf-8"))
            ts_status = status_data.get("ts_utc") or status_data.get("ts_et")
            status_note = f"status.json ts={ts_status}" if ts_status else "status.json parsed"
        except Exception as e:  # pragma: no cover - best effort logging
            status_note = f"status.json parse failed: {e}"  # type: ignore[assignment]

    lines = [
        "\n## Replay learning card",
        f"- Source file: {source_file.relative_to(ROOT)}",
        f"- Window UTC: {start.astimezone(timezone.utc).isoformat()} -> {end.astimezone(timezone.utc).isoformat()}",
        f"- Window ET: {_et_isoformat(start)} -> {_et_isoformat(end)}",
        f"- Filters: {filters_text}",
        f"- Event types: {dict(type_counts)}",
        f"- Symbols: {dict(symbol_counts)}",
        f"- Status: {status_note}",
        "- Recent highlights:",
    ]

    if highlight_lines:
        lines.extend(highlight_lines)
    else:
        lines.append("  (none)")

    lines.extend(
        [
            "- You should check:",
            "  - .\\.venv\\Scripts\\python.exe .\\tools\\verify_smoke.py",
            "  - .\\.venv\\Scripts\\python.exe .\\tools\\verify_cooldown.py",
            "  - .\\.venv\\Scripts\\python.exe .\\tools\\tail_events.py --limit 20",
            "  - .\\.venv\\Scripts\\python.exe .\\tools\\replay_events.py --since-minutes 60 --limit 50 --stats",
            "",
        ]
    )

    try:
        with card_path.open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(line if line.endswith("\n") else f"{line}\n")
        print(f"[OK] learning card appended to {card_path.relative_to(ROOT)}")
    except Exception as e:  # pragma: no cover - best effort logging
        print(f"[WARN] failed to write learning card: {e}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay/inspect recent events")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="max events to show")
    parser.add_argument(
        "--since-minutes",
        type=float,
        default=DEFAULT_SINCE_MINUTES,
        help="only events within N minutes",
    )
    parser.add_argument("--symbol", help="filter by symbol")
    parser.add_argument("--type", dest="type", help="filter by event_type")
    parser.add_argument(
        "--severity",
        choices=["low", "med", "high"],
        help="filter by severity (low|med|high)",
    )
    parser.add_argument("--contains", help="case-insensitive substring search in message")
    parser.add_argument("--json", action="store_true", help="output raw json")
    parser.add_argument("--stats", action="store_true", help="show stats")
    parser.add_argument(
        "--require-events",
        action="store_true",
        help="exit 2 if no events matched",
    )
    parser.add_argument(
        "--write-learning-card",
        action="store_true",
        help="append a learning card to Data/learning_cards.md",
    )
    return parser.parse_args()


def main() -> None:
    configure_stdio_utf8()

    args = parse_args()

    cfg = load_config()
    logs_dir = get_logs_dir(cfg)
    logs_dir.mkdir(parents=True, exist_ok=True)

    latest = find_latest_events_file(logs_dir)
    if latest is None:
        print(f"No events file found in {logs_dir}")
        if args.require_events:
            sys.exit(2)
        return

    symbol = args.symbol.upper() if args.symbol else None
    event_type = args.type.upper() if args.type else None
    contains = args.contains.lower() if args.contains else None
    severity = args.severity.lower() if args.severity else None

    events = filter_events(
        iter_events(latest),
        symbol=symbol,
        event_type=event_type,
        severity=severity,
        since_minutes=args.since_minutes,
        contains=contains,
    )

    if not events:
        print(f"No events matched filters in {latest}")
        if args.require_events:
            print("FAIL: require-events set but no events found")
            sys.exit(2)
        return

    events = events[-args.limit :]

    if args.json:
        for ev in events:
            print(json.dumps(ev, ensure_ascii=False))
    else:
        for ev in events:
            print(_format_human(ev))

    if args.stats:
        _print_stats(events)

    if args.write_learning_card:
        _append_learning_card(events, logs_dir=logs_dir, source_file=latest, args=args)


if __name__ == "__main__":
    main()
