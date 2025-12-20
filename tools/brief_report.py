from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DEFAULT_LIMIT = 20


try:  # pragma: no cover - timezone fallback
    from zoneinfo import ZoneInfo

    UTC = ZoneInfo("UTC")
except Exception:  # pragma: no cover - fallback for older Python
    UTC = timezone.utc


def load_config() -> Dict[str, Any]:
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
        candidates.sort(key=lambda p: (_safe_mtime(p), p.name))
        return candidates[-1]

    legacy = logs_dir / "events.jsonl"
    if legacy.exists():
        return legacy
    return None


def load_status(logs_dir: Path) -> Dict[str, Any]:
    status_path = logs_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        with status_path.open("r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _parse_ts(ev: Dict[str, Any]) -> Optional[datetime]:
    ts_raw = ev.get("ts_utc") or ev.get("ts_et") or ev.get("ts")
    if not ts_raw:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_raw))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _load_events_with_lines(path: Path) -> List[Tuple[int, Dict[str, Any]]]:
    events: List[Tuple[int, Dict[str, Any]]] = []
    bad_lines = 0
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for idx, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append((idx, json.loads(line)))
            except Exception:
                bad_lines += 1
    if bad_lines:
        print(f"[WARN] skipped {bad_lines} bad line(s) in {path}", file=sys.stderr)
    return events


def _sort_events(events_with_lines: Sequence[Tuple[int, Dict[str, Any]]]) -> List[Tuple[int, Dict[str, Any]]]:
    def _key(item: Tuple[int, Dict[str, Any]]):
        line_no, ev = item
        ts = _parse_ts(ev)
        return (ts or datetime.min.replace(tzinfo=UTC), line_no)

    return sorted(events_with_lines, key=_key)


def _select_recent(events_with_lines: Sequence[Tuple[int, Dict[str, Any]]], limit: int) -> List[Tuple[int, Dict[str, Any]]]:
    sorted_events = _sort_events(events_with_lines)
    if not sorted_events:
        return []
    return sorted_events[-limit:]


def _format_evidence(path: Path, line_no: int, ev: Dict[str, Any]) -> str:
    event_id = ev.get("event_id")
    ts = ev.get("ts_utc") or ev.get("ts_et")
    id_part = f" event_id={event_id}" if event_id else ""
    ts_part = f" ts_utc={ts}" if ts else ""
    return f"[evidence: {path.name}#L{line_no}{id_part}{ts_part}]"


def _format_fact(path: Path, line_no: int, ev: Dict[str, Any]) -> str:
    ts = _parse_ts(ev)
    ts_text = ts.astimezone(UTC).isoformat() if ts else "?"
    summary_parts = [
        ev.get("event_type", "?"),
        str(ev.get("symbol") or "-"),
        ev.get("severity", "?"),
    ]
    prefix = " ".join(summary_parts)
    message = str(ev.get("message", "")).replace("\n", " | ")
    metrics = ev.get("metrics")
    metrics_part = ""
    if isinstance(metrics, dict) and metrics:
        preview = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:4])
        metrics_part = f" | metrics: {preview}"
    evidence = _format_evidence(path, line_no, ev)
    return f"- [{ts_text}] {prefix}: {message}{metrics_part} {evidence}"


def _collect_analysis(events: Sequence[Tuple[int, Dict[str, Any]]], source_path: Optional[Path]) -> List[str]:
    if not events:
        return ["- No events available for aggregation; unable to compute counts."]

    evidence_label = source_path.name if source_path else "events.jsonl"
    counters = {
        "event_type": Counter(),
        "symbol": Counter(),
        "severity": Counter(),
    }
    for _, ev in events:
        counters["event_type"][ev.get("event_type") or "-"] += 1
        counters["symbol"][ev.get("symbol") or "-"] += 1
        counters["severity"][ev.get("severity") or "-"] += 1

    lines: List[str] = []
    for label, counter in counters.items():
        most_common = counter.most_common(5)
        if not most_common:
            continue
        evidence = f" [evidence: {evidence_label}]"
        parts = ", ".join(f"{k or '-'}={v}" for k, v in most_common)
        lines.append(f"- Top {label} counts: {parts}{evidence}")

    if not lines:
        lines.append("- Unable to derive statistics from the current dataset.")
    return lines


def _collect_hypotheses(events: Sequence[Tuple[int, Dict[str, Any]]], source_path: Optional[Path]) -> List[str]:
    if not events:
        return ["- No events to hypothesize from; any pattern remains unconfirmed."]

    evidence_label = source_path.name if source_path else "events.jsonl"
    hypothesis_lines: List[str] = []
    total = len(events)
    by_type = Counter(ev.get("event_type") or "-" for _, ev in events)
    top_type, top_type_count = by_type.most_common(1)[0]
    evidence = f" [evidence: {evidence_label}]"
    hypothesis_lines.append(
        f"- {top_type} events appear most frequently ({top_type_count}/{total}); this may indicate instrumentation focus, but alternative explanations remain (sampling bias, repeated retries). Uncertain. {evidence}"
    )

    by_symbol = Counter(str(ev.get("symbol") or "-").upper() for _, ev in events)
    symbol, symbol_count = by_symbol.most_common(1)[0]
    hypothesis_lines.append(
        f"- Activity clusters around symbol {symbol} ({symbol_count}/{total}); unclear if this is due to monitoring scope or genuine incident concentration. Needs validation. {evidence}"
    )

    severity_counter = Counter(str(ev.get("severity") or "-").lower() for _, ev in events)
    severe_label, severe_count = severity_counter.most_common(1)[0]
    hypothesis_lines.append(
        f"- Severity skews toward '{severe_label}' ({severe_count}/{total}); interpretation is tentative because severity labels may differ by source. {evidence}"
    )

    return hypothesis_lines


def _format_next_tests(logs_dir: Path, source_path: Optional[Path]) -> List[str]:
    replay_source = source_path if source_path else logs_dir / "events.jsonl"
    commands = [
        f".\\.venv\\Scripts\\python.exe .\\tools\\verify_smoke.py --limit 20",
        f".\\.venv\\Scripts\\python.exe .\\tools\\verify_cooldown.py",
        f".\\.venv\\Scripts\\python.exe .\\tools\\replay_events.py --path {replay_source.as_posix()} --limit 10",
    ]
    return [f"- `{cmd}`" for cmd in commands]


def build_report(
    *,
    logs_dir: Path,
    status: Dict[str, Any],
    events_path: Optional[Path],
    events: Sequence[Tuple[int, Dict[str, Any]]],
    limit: int,
    report_date: date,
) -> str:
    heading = "# Evidence-driven brief (READ_ONLY)"
    intro_lines = [heading, ""]

    if events_path:
        intro_lines.append(f"Source events: {events_path}")
    else:
        intro_lines.append("Source events: none found (report generated for visibility)")
    if status:
        intro_lines.append(f"Status snapshot keys: {', '.join(sorted(status.keys()))} [evidence: status.json]")
    intro_lines.append("")

    recent_events = _select_recent(events, limit)

    lines: List[str] = intro_lines
    lines.append("## Facts")
    if recent_events:
        for line_no, ev in recent_events:
            lines.append(_format_fact(events_path or Path("events.jsonl"), line_no, ev))
    else:
        lines.append("- No events found; brief is informational only.")
    lines.append("")

    lines.append("## Analysis")
    lines.extend(_collect_analysis(recent_events, events_path))
    lines.append("")

    lines.append("## Hypotheses")
    lines.extend(_collect_hypotheses(recent_events, events_path))
    lines.append("")

    lines.append("## Next tests")
    lines.extend(_format_next_tests(logs_dir, events_path))
    lines.append("")

    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate evidence-driven brief report")
    parser.add_argument("--logs-dir", type=Path, help="Path to Logs directory")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Number of recent events to include")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "run_reports", help="Directory for generated reports")
    parser.add_argument("--date", type=str, help="Override report date (YYYY-MM-DD)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    cfg = load_config()
    logs_dir = args.logs_dir or get_logs_dir(cfg)
    logs_dir.mkdir(parents=True, exist_ok=True)

    events_path = find_latest_events_file(logs_dir)
    status = load_status(logs_dir)

    events_with_lines: List[Tuple[int, Dict[str, Any]]] = []
    if events_path and events_path.exists():
        events_with_lines = _load_events_with_lines(events_path)
    else:
        print(f"[INFO] No events file found under {logs_dir}; continuing with empty dataset.")

    report_date_str = args.date or date.today().isoformat()
    report_date = date.fromisoformat(report_date_str)

    report_text = build_report(
        logs_dir=logs_dir,
        status=status,
        events_path=events_path,
        events=events_with_lines,
        limit=args.limit,
        report_date=report_date,
    )

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{report_date.isoformat()}.md"
    output_path.write_text(report_text, encoding="utf-8")
    print(f"[INFO] Report written to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
