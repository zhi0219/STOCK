from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except Exception:  # pragma: no cover - fallback when dependency missing
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stdio_utf8 import configure_stdio_utf8
CONFIG_PATH = ROOT / "config.yaml"
DEFAULT_LIMIT = 30
DEFAULT_SINCE_MINUTES = 1440.0
DEFAULT_MAX_CHARS = 12000


@dataclass
class EvidenceCandidate:
    score: int
    ts: Optional[datetime]
    line_no: int
    message: str
    event_type: str
    symbol: str
    source_path: Path

    def evidence_tag(self) -> str:
        ts_str = format_ts(self.ts)
        return (
            f"[evidence: {self.source_path.name}#L{self.line_no} "
            f"ts_utc={ts_str} event_type={self.event_type} symbol={self.symbol}]"
        )

    def ts_sort_key(self) -> float:
        return self.ts.timestamp() if self.ts else 0.0


def load_config() -> Dict[str, Any]:
    if yaml is None or not CONFIG_PATH.exists():
        if yaml is None:
            print("[WARN] PyYAML not available; using empty config", file=sys.stderr)
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


def tokenize(text: str) -> List[str]:
    return [tok for tok in re.split(r"[^a-zA-Z0-9]+", text.lower()) if tok]


def format_ts(ts: Optional[datetime]) -> str:
    if ts is None:
        return "?"
    ts_utc = ts.astimezone(timezone.utc)
    return ts_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def score_text(tokens: Sequence[str], text: str) -> int:
    if not tokens:
        return 0
    lowered = text.lower()
    score = 0
    for tok in tokens:
        score += lowered.count(tok)
    return score


def iter_events(path: Path) -> Iterable[Tuple[int, Dict[str, Any]]]:
    bad_lines = 0
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                bad_lines += 1
                continue
            yield line_no, obj
    if bad_lines:
        print(f"[WARN] skipped {bad_lines} bad line(s) in {path}", file=sys.stderr)


def within_window(ts: Optional[datetime], since_minutes: float) -> bool:
    if ts is None:
        return True
    threshold = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    return ts >= threshold


def extract_message(ev: Dict[str, Any]) -> str:
    message = str(ev.get("message", "")).replace("\n", " | ")
    metrics = ev.get("metrics")
    metrics_part = ""
    if isinstance(metrics, dict) and metrics:
        metrics_preview = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:4])
        metrics_part = f" | metrics: {metrics_preview}"
    return f"{message}{metrics_part}"


def build_event_candidates(
    path: Path,
    *,
    tokens: Sequence[str],
    since_minutes: float,
    type_filters: Optional[Sequence[str]],
    symbol_filters: Optional[Sequence[str]],
) -> List[EvidenceCandidate]:
    allowed_types = {t.upper() for t in type_filters} if type_filters else None
    allowed_symbols = {s.upper() for s in symbol_filters} if symbol_filters else None
    candidates: List[EvidenceCandidate] = []
    for line_no, ev in iter_events(path):
        event_type = str(ev.get("event_type", "")).upper() or "?"
        symbol = str(ev.get("symbol", "")).upper() or "-"
        if allowed_types and event_type not in allowed_types:
            continue
        if allowed_symbols and symbol not in allowed_symbols:
            continue
        ts = _parse_ts(ev.get("ts_utc") or ev.get("ts_et"))
        if not within_window(ts, since_minutes):
            continue
        message = extract_message(ev)
        score = score_text(tokens, f"{event_type} {symbol} {message}")
        candidates.append(
            EvidenceCandidate(
                score=score,
                ts=ts,
                line_no=line_no,
                message=message,
                event_type=event_type or "?",
                symbol=symbol or "-",
                source_path=path,
            )
        )
    return candidates


def build_run_report_candidates(
    *, tokens: Sequence[str], since_minutes: float
) -> List[EvidenceCandidate]:
    reports_dir = ROOT / "run_reports"
    if not reports_dir.exists():
        return []
    candidates = []
    for report_path in sorted(reports_dir.glob("*.md")):
        mtime = _safe_mtime(report_path)
        threshold = datetime.now().timestamp() - since_minutes * 60
        if mtime < threshold:
            continue
        try:
            lines = report_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception as exc:
            print(f"[WARN] failed to read {report_path}: {exc}", file=sys.stderr)
            continue
        report_ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
        for idx, line in enumerate(lines, start=1):
            clean_line = line.strip()
            if not clean_line:
                continue
            score = score_text(tokens, clean_line)
            candidates.append(
                EvidenceCandidate(
                    score=score,
                    ts=report_ts,
                    line_no=idx,
                    message=clean_line,
                    event_type="RUN_REPORT",
                    symbol="-",
                    source_path=report_path,
                )
            )
    return candidates


def _slugify(text: str) -> str:
    slug_chars: List[str] = []
    for ch in text.lower():
        if ch.isalnum():
            slug_chars.append(ch)
        elif ch.isspace() or ch in {"-", "_", "."}:
            slug_chars.append("-")
    slug = "".join(slug_chars).strip("-")
    return slug or "question"


def read_status_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[WARN] failed to read {path}: {exc}", file=sys.stderr)
        return None


def summarize_status(path: Optional[Path], status: Optional[Dict[str, Any]]) -> List[str]:
    if not status or not path:
        return ["(No status snapshot found.)"]
    lines = [f"Source: {path.name}"]
    for key in sorted(status.keys()):
        value = status.get(key)
        lines.append(f"- {key}: {value}")
    return lines


def system_rules_block() -> List[str]:
    return [
        "READ_ONLY: no trading/orders/login or 2FA/captcha/region bypass attempts.",
        "Cite evidence with stable [evidence: ...] tags for facts/analysis/hypotheses.",
        "Observational only; no buy/sell/target/position guidance.",
        "Split Facts/Analysis/Hypotheses and call out evidence gaps before speculating.",
    ]


def format_candidate(c: EvidenceCandidate) -> str:
    ts_str = format_ts(c.ts)
    body = c.message.replace("\n", " | ")
    return f"- [{ts_str}] {c.event_type} {c.symbol}: {body} {c.evidence_tag()}"


def build_output_lines(
    *,
    question: str,
    keywords: Sequence[str],
    candidates: List[EvidenceCandidate],
    selected: List[EvidenceCandidate],
    status_lines: List[str],
    limit: int,
    since_minutes: float,
    max_chars: int,
) -> Tuple[List[str], bool]:
    keywords_display = ", ".join(keywords) if keywords else "(no keywords extracted)"
    lines: List[str] = []
    lines.append(f"# Evidence mini-pack ({datetime.now(timezone.utc).astimezone().isoformat()})")
    lines.append("")

    lines.append("## A) SYSTEM RULES")
    lines.extend(f"- {rule}" for rule in system_rules_block())
    lines.append("")

    lines.append("## B) STATUS SNAPSHOT")
    lines.extend(status_lines)
    lines.append("")

    lines.append("## C) SELECTED EVIDENCE")
    lines.append(f"- Keywords used: {keywords_display}")
    selection_line_index = len(lines)
    lines.append(
        "- Evaluated {}; selected {}/{}; window {:.0f} min.".format(
            len(candidates), 0, limit, since_minutes
        )
    )
    trunc_line_index = len(lines)
    lines.append(f"- Max chars: {max_chars}; truncated: no")
    lines.append("")

    tail_lines = [
        "## D) QUESTION",
        question,
        "",
        "## E) REQUIRED OUTPUT FORMAT",
        "- 结论要点（每条引用 evidence）",
        "- 硬事实（逐条引用 evidence）",
        "- 主流/反方观点（注明 evidence 来源或缺口）",
        "- 风险提醒（注明证据或缺失情况）",
        "",
    ]

    truncated = False
    evidence_added = False
    selected_count = 0
    for candidate in selected:
        candidate_line = format_candidate(candidate)
        projected = lines + [candidate_line, ""] + tail_lines
        if len("\n".join(projected)) > max_chars:
            truncated = True
            break
        lines.append(candidate_line)
        evidence_added = True
        selected_count += 1

    if not evidence_added:
        placeholder = "(No matching evidence found in the requested window.)"
        projected = lines + [placeholder, ""] + tail_lines
        if len("\n".join(projected)) <= max_chars:
            lines.append(placeholder)
        else:
            truncated = True

    lines.append("")
    lines.extend(tail_lines)

    if selection_line_index < len(lines):
        lines[selection_line_index] = (
            "- Evaluated {}; selected {}/{}; window {:.0f} min.".format(
                len(candidates), selected_count, limit, since_minutes
            )
        )

    output_text = "\n".join(lines)
    if len(output_text) > max_chars:
        truncated = True
        # Final safety trim while keeping earlier content
        trimmed_lines: List[str] = []
        for line in lines:
            tentative = "\n".join(trimmed_lines + [line])
            if len(tentative) > max_chars:
                break
            trimmed_lines.append(line)
        lines = trimmed_lines

    if truncated and trunc_line_index < len(lines):
        lines[trunc_line_index] = f"- Max chars: {max_chars}; truncated: yes"
    output_text = "\n".join(lines)
    return output_text.split("\n"), truncated


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select evidence snippets for a given question")
    parser.add_argument("--question", required=True, help="User question prompting the evidence pack")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum number of evidence snippets")
    parser.add_argument("--since-minutes", type=float, default=DEFAULT_SINCE_MINUTES, help="Lookback window in minutes")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Maximum output characters")
    parser.add_argument("--types", help="Comma-separated event_type filters (e.g., MOVE,DATA_STALE)")
    parser.add_argument("--symbols", help="Comma-separated symbol filters (e.g., AAPL,MSFT)")
    parser.add_argument("--out", help="Optional output path; defaults to evidence_packs/YYYY-MM-DD/HHMMSS_<slug>.md")
    parser.add_argument(
        "--require-evidence",
        action="store_true",
        help="Exit with code 2 when no events file is available",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    configure_stdio_utf8()
    args = parse_args(argv)
    question_tokens = tokenize(args.question)

    cfg = load_config()
    logs_dir = get_logs_dir(cfg)
    logs_dir.mkdir(parents=True, exist_ok=True)

    events_path = find_latest_events_file(logs_dir)
    if events_path is None:
        print(f"No events file found in {logs_dir}")
        if args.require_evidence:
            return 2
        return 0

    status_path = find_latest_status(logs_dir)
    status_data = read_status_snapshot(status_path) if status_path else None
    status_lines = summarize_status(status_path, status_data)

    type_filters = [t.strip() for t in args.types.split(",") if t.strip()] if args.types else None
    symbol_filters = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None

    event_candidates = build_event_candidates(
        events_path,
        tokens=question_tokens,
        since_minutes=args.since_minutes,
        type_filters=type_filters,
        symbol_filters=symbol_filters,
    )
    report_candidates = build_run_report_candidates(tokens=question_tokens, since_minutes=args.since_minutes)
    all_candidates = event_candidates + report_candidates

    all_candidates.sort(key=lambda c: (c.score, c.ts_sort_key(), c.line_no), reverse=True)

    selected_candidates = all_candidates[: max(args.limit, 0)]

    output_lines, truncated = build_output_lines(
        question=args.question,
        keywords=question_tokens,
        candidates=all_candidates,
        selected=selected_candidates,
        status_lines=status_lines,
        limit=args.limit,
        since_minutes=args.since_minutes,
        max_chars=args.max_chars,
    )

    output_text = "\n".join(output_lines)

    now = datetime.now().astimezone()
    date_part = now.strftime("%Y-%m-%d")
    time_part = now.strftime("%H%M%S")
    slug = _slugify(args.question)[:60]
    out_path = Path(args.out) if args.out else ROOT / "evidence_packs" / date_part / f"{time_part}_{slug}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output_text, encoding="utf-8")

    print(output_text)
    print(f"\nSaved to: {out_path}")
    print("PASS: evidence mini-pack generated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
