from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_yaml_spec = importlib.util.find_spec("yaml")
yaml = importlib.import_module("yaml") if _yaml_spec else None

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stdio_utf8 import configure_stdio_utf8
CONFIG_PATH = ROOT / "config.yaml"
DEFAULT_LIMIT = 80
DEFAULT_SINCE_MINUTES = 1440


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


def find_latest_status(logs_dir: Path) -> Optional[Path]:
    candidates = list(logs_dir.glob("status*.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: (_safe_mtime(p), p.name))
    return candidates[-1]


def find_latest_events_file(logs_dir: Path) -> Optional[Path]:
    candidates = list(logs_dir.glob("events_*.jsonl"))
    if candidates:
        candidates.sort(key=lambda p: (_safe_mtime(p), p.name))
        return candidates[-1]
    legacy = logs_dir / "events.jsonl"
    if legacy.exists():
        return legacy
    return None


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


def load_events(
    path: Path, *, since_minutes: float, limit: int
) -> Tuple[List[Tuple[int, Dict[str, Any]]], int]:
    bad_lines = 0
    since_dt = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    events: List[Tuple[int, Dict[str, Any]]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                bad_lines += 1
                continue
            ts = _parse_ts(ev)
            if ts is not None and ts < since_dt:
                continue
            events.append((line_no, ev))
    if bad_lines:
        print(f"[WARN] skipped {bad_lines} bad line(s) in {path}", file=sys.stderr)
    if len(events) > limit:
        events = events[-limit:]
    return events, bad_lines


def _slugify(text: str) -> str:
    slug_chars: List[str] = []
    for ch in text.lower():
        if ch.isalnum():
            slug_chars.append(ch)
        elif ch.isspace() or ch in {"-", "_", "."}:
            slug_chars.append("-")
    slug = "".join(slug_chars).strip("-")
    return slug or "question"


def _format_event_line(events_path: Path, line_no: int, ev: Dict[str, Any]) -> str:
    ts = _parse_ts(ev)
    ts_str = ts.isoformat() if ts else "?"
    event_type = ev.get("event_type", "?")
    symbol = ev.get("symbol") or "-"
    severity = ev.get("severity") or "-"
    message = str(ev.get("message", "")).replace("\n", " | ")
    metrics = ev.get("metrics")
    metrics_part = ""
    if isinstance(metrics, dict) and metrics:
        preview = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:4])
        metrics_part = f" | metrics: {preview}"
    evidence_tag = f"[evidence: {events_path.name}#L{line_no} ts_utc={ts_str}]"
    return f"- [{ts_str}] {event_type} {symbol} {severity}: {message}{metrics_part} {evidence_tag}"


def _format_status(status_path: Path, status: Dict[str, Any]) -> str:
    lines = [f"Source: {status_path.name}"]
    for key in sorted(status.keys()):
        value = status.get(key)
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _system_rules_block() -> str:
    rules = [
        "READ_ONLY forever: never trade, place orders, log in to brokers, or bypass verification/2FA/captcha/region controls.",
        "Separate Facts, Analysis, and Hypotheses; every conclusion must cite evidence from events/status with [evidence: ...] markers.",
        "No buy/sell/target-price/position-size guidance; keep responses observational only.",
        "If evidence is missing or insufficient, clearly state the gap before giving any analysis.",
    ]
    return "\n".join(f"- {rule}" for rule in rules)


def build_markdown(
    *,
    question: str,
    status_path: Optional[Path],
    status_data: Optional[Dict[str, Any]],
    events_path: Optional[Path],
    events: List[Tuple[int, Dict[str, Any]]],
    reports_text: Optional[str],
    limit: int,
    since_minutes: float,
    evidence_pack_text: Optional[str] = None,
) -> str:
    lines: List[str] = []
    now = datetime.now(timezone.utc).astimezone()
    lines.append(f"# AI 问答证据包 ({now.isoformat()})")
    lines.append("")
    lines.append("## A) SYSTEM RULES")
    lines.append(_system_rules_block())
    lines.append("")

    lines.append("## B) EVIDENCE")
    lines.append(f"- Status snapshot: {'available' if status_data else 'missing'}")
    lines.append(f"- Events window: last {since_minutes:.0f} minutes, showing up to {limit} entries")
    lines.append("")

    lines.append("### Status")
    if status_data and status_path:
        lines.append(_format_status(status_path, status_data))
    else:
        lines.append("(No status snapshot found.)")
    lines.append("")

    lines.append("### Events")
    if events and events_path:
        for line_no, ev in events:
            lines.append(_format_event_line(events_path, line_no, ev))
    else:
        lines.append("(No events found in the requested window.)")
    lines.append("")

    if evidence_pack_text:
        lines.append("### Embedded evidence pack")
        lines.append(evidence_pack_text.strip())
        lines.append("")

    if reports_text:
        lines.append("### Latest run report")
        lines.append(reports_text)
        lines.append("")

    lines.append("## C) QUESTION")
    lines.append(question)
    lines.append("")

    lines.append("## D) REQUIRED OUTPUT FORMAT")
    lines.append("- 结论要点（每条引用 evidence）")
    lines.append("- 硬事实（逐条引用 evidence）")
    lines.append("- 主流/反方观点（注明 evidence 来源或缺口）")
    lines.append("- 风险提醒（注明证据或缺失情况）")
    lines.append("")

    return "\n".join(lines)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[WARN] failed to read {path}: {exc}", file=sys.stderr)
        return None


def _read_latest_report() -> Optional[Tuple[Path, str]]:
    reports_dir = ROOT / "run_reports"
    if not reports_dir.exists():
        return None
    candidates = [p for p in reports_dir.glob("*.md") if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (_safe_mtime(p), p.name))
    latest = candidates[-1]
    try:
        return latest, latest.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] failed to read {latest}: {exc}", file=sys.stderr)
        return None


def write_packet(markdown: str, question: str) -> Path:
    now = datetime.now().astimezone()
    date_part = now.strftime("%Y-%m-%d")
    time_part = now.strftime("%H%M%S")
    slug = _slugify(question)[:60]
    out_dir = ROOT / "qa_packets" / date_part
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{time_part}_{slug}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an AI evidence packet for Q&A")
    parser.add_argument("--question", required=True, help="User question to include in the packet")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum number of events to include")
    parser.add_argument(
        "--since-minutes",
        type=float,
        default=DEFAULT_SINCE_MINUTES,
        help="Look back window in minutes for events",
    )
    parser.add_argument(
        "--require-evidence",
        action="store_true",
        help="Exit non-zero if status or events are missing",
    )
    parser.add_argument(
        "--from-evidence-pack",
        type=Path,
        help="Optional path to an existing evidence pack to embed into the AI packet",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    configure_stdio_utf8()
    args = parse_args(argv)

    cfg = load_config()
    logs_dir = get_logs_dir(cfg)
    logs_dir.mkdir(parents=True, exist_ok=True)

    status_path = find_latest_status(logs_dir)
    status_data = _read_json(status_path) if status_path else None

    events_path = find_latest_events_file(logs_dir)
    events: List[Tuple[int, Dict[str, Any]]] = []
    if events_path:
        events, _ = load_events(events_path, since_minutes=args.since_minutes, limit=args.limit)

    report_pair = _read_latest_report()
    reports_text = None
    if report_pair:
        report_path, report_content = report_pair
        reports_text = f"Source: {report_path.name}\n\n" + report_content

    evidence_pack_text = None
    if args.from_evidence_pack:
        try:
            evidence_pack_text = args.from_evidence_pack.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"[WARN] failed to read evidence pack {args.from_evidence_pack}: {exc}", file=sys.stderr)

    markdown = build_markdown(
        question=args.question,
        status_path=status_path,
        status_data=status_data,
        events_path=events_path,
        events=events,
        reports_text=reports_text,
        limit=args.limit,
        since_minutes=args.since_minutes,
        evidence_pack_text=evidence_pack_text,
    )

    packet_path = write_packet(markdown, args.question)
    print(markdown)
    print(f"\nSaved to: {packet_path}")
    print(f"PACKET_PATH={packet_path}")

    if args.require_evidence and (status_data is None or not events):
        print("[WARN] Evidence missing: status and/or events not found", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
