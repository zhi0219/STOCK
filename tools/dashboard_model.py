from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


UTC = timezone.utc


@dataclass
class EventRecord:
    data: Dict[str, Any]
    path: Path
    line_no: int
    ts: Optional[datetime]

    @property
    def evidence(self) -> str:
        return f"{self.path.name}#L{self.line_no}" if self.line_no else self.path.name


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _find_latest_events_file(logs_dir: Path) -> Optional[Path]:
    candidates = list(logs_dir.glob("events_*.jsonl"))
    if candidates:
        candidates.sort(key=lambda p: (_safe_mtime(p), p.name))
        return candidates[-1]
    legacy = logs_dir / "events.jsonl"
    if legacy.exists():
        return legacy
    return None


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def load_latest_status(logs_dir: Path) -> Dict[str, Any] | None:
    status_path = logs_dir / "status.json"
    if not status_path.exists():
        return None
    try:
        with status_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def load_recent_events(logs_dir: Path, since_minutes: int) -> List[Dict[str, Any]]:
    path = _find_latest_events_file(logs_dir)
    if not path:
        return []

    cutoff = datetime.now(UTC) - timedelta(minutes=max(since_minutes, 0))
    events: List[EventRecord] = []
    bad_lines = 0
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for idx, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                bad_lines += 1
                continue
            ts = _parse_ts(data.get("ts_utc") or data.get("ts_et") or data.get("ts"))
            if ts and ts < cutoff:
                continue
            events.append(EventRecord(data=data, path=path, line_no=idx, ts=ts))

    events.sort(key=lambda ev: (ev.ts or datetime.min.replace(tzinfo=UTC), ev.line_no))

    result: List[Dict[str, Any]] = []
    for record in events:
        enriched = dict(record.data)
        enriched["__path"] = str(record.path)
        enriched["__line__"] = record.line_no
        if record.ts:
            enriched["__ts"] = record.ts
        enriched["__evidence"] = record.evidence
        result.append(enriched)

    if bad_lines:
        result.append(
            {
                "event_type": "WARN",
                "message": f"skipped {bad_lines} bad line(s) while reading {path.name}",
                "__path": str(path),
                "__line__": 0,
            }
        )

    return result


def _format_seconds(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value < 1:
        return f"{value:.2f}s"
    if value < 60:
        return f"{value:.0f}s"
    minutes = value / 60
    return f"{minutes:.1f}m"


def _light(status: str, value: str, threshold: str, evidence: str) -> Dict[str, str]:
    return {
        "status": status,
        "value": value,
        "threshold": threshold,
        "evidence": evidence,
    }


def _extract_last_event(events: Iterable[Dict[str, Any]], event_type: str) -> Optional[Dict[str, Any]]:
    for ev in sorted(events, key=lambda e: e.get("__ts") or datetime.min.replace(tzinfo=UTC), reverse=True):
        if ev.get("event_type") == event_type:
            return ev
    return None


def _count_events(events: Iterable[Dict[str, Any]], types: Iterable[str], window_minutes: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
    types_set = {t.upper() for t in types}
    count = 0
    for ev in events:
        ts = ev.get("__ts")
        if not ts or ts < cutoff:
            continue
        if str(ev.get("event_type") or "").upper() in types_set:
            count += 1
    return count


def compute_health(
    status: Dict[str, Any] | None, events: List[Dict[str, Any]], supervisor_state: Dict[str, Any] | None
) -> Dict[str, Any]:
    cfg = (status or {}).get("config", {}) or {}
    poll_seconds = float(cfg.get("poll_seconds", 60))
    stale_seconds = float(cfg.get("stale_seconds", poll_seconds * 3))
    flat_repeats = int(cfg.get("flat_repeats", 0)) or None

    quotes_info = (status or {}).get("quotes") or {}
    file_age = quotes_info.get("file_age_s")
    if file_age is None:
        try:
            quotes_path = Path(quotes_info.get("path")) if quotes_info.get("path") else None
            if quotes_path and quotes_path.exists():
                file_age = max(0.0, datetime.now().timestamp() - quotes_path.stat().st_mtime)
        except Exception:
            file_age = None

    lights = {}
    if file_age is None:
        lights["data_fresh"] = _light("unknown", "?", f"green < {2*poll_seconds:.0f}s", "status.json quotes.file_age_s missing")
    else:
        if file_age < 2 * poll_seconds:
            status_color = "green"
        elif file_age < max(6 * poll_seconds, stale_seconds):
            status_color = "yellow"
        else:
            status_color = "red"
        lights["data_fresh"] = _light(
            status_color,
            _format_seconds(float(file_age)),
            f"green < {2*poll_seconds:.0f}s; yellow < {max(6*poll_seconds, stale_seconds):.0f}s",
            quotes_info.get("path", "status.json quotes.file_age_s"),
        )

    data_flat_event = _extract_last_event(events, "DATA_FLAT")
    if data_flat_event:
        metrics = data_flat_event.get("metrics") or {}
        run_len = metrics.get("run_len")
        threshold = metrics.get("threshold") or flat_repeats or 0
        status_color = "yellow" if run_len and threshold and run_len >= threshold else "green"
        lights["data_flat"] = _light(
            status_color,
            f"run_len={run_len or '?'}",
            f"threshold={threshold or '?'}",
            data_flat_event.get("__evidence", "events.jsonl"),
        )
    else:
        label = "status.json flat indicator missing" if flat_repeats is None else f"no DATA_FLAT in {flat_repeats} loops"
        lights["data_flat"] = _light("unknown", "?", f"threshold={flat_repeats or '?'}", label)

    sources = (supervisor_state or {}).get("sources") or {}
    quotes_running = sources.get("quotes", {}).get("running")
    alerts_running = sources.get("alerts", {}).get("running")
    if quotes_running is None and alerts_running is None:
        lights["system_alive"] = _light("unknown", "?", "running flags", "supervisor_state.json missing")
    else:
        if quotes_running and alerts_running:
            color = "green"
        elif quotes_running or alerts_running:
            color = "yellow"
        else:
            color = "red"
        lights["system_alive"] = _light(
            color,
            f"quotes={quotes_running} alerts={alerts_running}",
            "expect both running",
            "supervisor_state.json sources",
        )

    cards: List[Dict[str, Any]] = []
    watchlist = (status or {}).get("config", {}).get("watchlist")
    watchlist_count: Optional[int] = None
    if isinstance(watchlist, list):
        watchlist_count = len(watchlist)
    elif isinstance(watchlist, str):
        watchlist_count = len([x for x in watchlist.split(",") if x.strip()])
    cards.append(
        {
            "label": "Watchlist symbols",
            "value": watchlist_count if watchlist_count is not None else "unknown",
            "source": "status.config.watchlist" if watchlist is not None else "config missing",
        }
    )

    move_count = _count_events(events, {"MOVE"}, 60)
    data_count = _count_events(events, {"DATA_STALE", "DATA_MISSING", "DATA_FLAT"}, 60)
    cards.append({"label": "MOVE events (60m)", "value": move_count, "source": "events last 60m"})
    cards.append({"label": "DATA_* (60m)", "value": data_count, "source": "events last 60m"})

    cooldown = cfg.get("cooldown_seconds")
    alerts_start = _extract_last_event(events, "ALERTS_START")
    if alerts_start and (alerts_start.get("metrics") or {}).get("cooldown_seconds"):
        cooldown = alerts_start["metrics"]["cooldown_seconds"]
    cards.append({"label": "cooldown_seconds", "value": cooldown or "unknown", "source": "status/config or ALERTS_START"})

    latest_event = None
    if events:
        latest_event = sorted(events, key=lambda e: e.get("__ts") or datetime.min.replace(tzinfo=UTC))[-1]
    cards.append(
        {
            "label": "Last event time",
            "value": (latest_event.get("ts_et") or latest_event.get("ts_utc")) if latest_event else "unknown",
            "source": latest_event.get("__evidence", "events.jsonl") if latest_event else "events",
        }
    )

    last_ai = _extract_last_event(events, "AI_ANSWER")
    cards.append(
        {
            "label": "Last AI_ANSWER",
            "value": (last_ai.get("ts_et") or last_ai.get("ts_utc")) if last_ai else "none",
            "source": last_ai.get("__evidence", "events.jsonl") if last_ai else "events",
        }
    )

    evidence_lines = []
    if status and status.get("ts_utc"):
        evidence_lines.append(f"status.json ts_utc={status['ts_utc']}")
    if events:
        evidence_lines.append(f"events window={len(events)} rows from {Path(events[0].get('__path', 'events.jsonl')).name}")
    if supervisor_state and supervisor_state.get("ts_utc"):
        evidence_lines.append(f"supervisor_state ts_utc={supervisor_state['ts_utc']}")

    return {"lights": lights, "cards": cards, "evidence": "\n".join(evidence_lines)}


def _format_time_et(ts: Optional[datetime]) -> str:
    if not ts:
        return "?"
    try:
        return ts.astimezone().strftime("%H:%M:%S")
    except Exception:
        return ts.isoformat()


def _short_message(message: str, limit: int = 120) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


def compute_event_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ev in events:
        ts = ev.get("__ts")
        ts_et = _format_time_et(ts)
        event_type = str(ev.get("event_type") or "?")
        symbol = str(ev.get("symbol") or "-").upper()
        severity = str(ev.get("severity") or "-")
        metrics = ev.get("metrics") or {}
        key_metric = ""
        if event_type == "MOVE":
            move_pct = metrics.get("move_pct")
            threshold = metrics.get("threshold")
            key_metric = f"move={move_pct}% thr={threshold}" if move_pct is not None else "move"
        elif event_type in {"DATA_STALE", "DATA_MISSING"}:
            age = metrics.get("age_sec") or metrics.get("age")
            key_metric = f"age={age}s" if age is not None else "age?"
        elif event_type == "DATA_FLAT":
            run_len = metrics.get("run_len")
            threshold = metrics.get("threshold")
            key_metric = f"flat={run_len}/{threshold}" if run_len is not None else "flat"
        elif metrics:
            first_items = list(metrics.items())[:2]
            key_metric = ", ".join(f"{k}={v}" for k, v in first_items)

        message = _short_message(str(ev.get("message") or ""))
        rows.append(
            {
                "ts_et": ts_et,
                "event_type": event_type,
                "symbol": symbol,
                "severity": severity,
                "key_metric": key_metric,
                "message": message,
                "evidence": ev.get("__evidence") or ev.get("__path"),
                "raw": ev,
            }
        )
    return rows


def compute_move_leaderboard(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(minutes=60)
    moves: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        if str(ev.get("event_type") or "").upper() != "MOVE":
            continue
        ts = ev.get("__ts")
        if not ts or ts < cutoff:
            continue
        symbol = str(ev.get("symbol") or "-").upper()
        moves.setdefault(symbol, []).append(ev)

    leaderboard: List[Dict[str, Any]] = []
    for symbol, sym_events in moves.items():
        sorted_events = sorted(sym_events, key=lambda e: e.get("__ts") or datetime.min.replace(tzinfo=UTC))
        latest = sorted_events[-1]
        latest_move = (latest.get("metrics") or {}).get("move_pct")
        move_values = []
        for ev in sym_events:
            move_pct = (ev.get("metrics") or {}).get("move_pct")
            if move_pct is not None:
                try:
                    move_values.append(abs(float(move_pct)))
                except Exception:
                    continue
        max_abs = max(move_values) if move_values else None
        leaderboard.append(
            {
                "symbol": symbol,
                "last_move_pct": latest_move,
                "move_count_60m": len(sym_events),
                "max_abs_move_60m": max_abs,
                "evidence": latest.get("__evidence", "events"),
            }
        )

    leaderboard.sort(key=lambda row: (row.get("max_abs_move_60m") or 0, row.get("move_count_60m", 0)), reverse=True)
    return leaderboard

