"""Generate a concise Chinese summary from status.json and recent events.

This module is intentionally stdlib-only to stay lightweight and deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"


@dataclass
class Evidence:
    description: str
    marker: str

    def format(self) -> str:
        return f"{self.description} [evidence: {self.marker}]"


def _latest_file(pattern: str) -> Optional[Path]:
    candidates = sorted(LOGS_DIR.glob(pattern))
    return candidates[-1] if candidates else None


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _system_status_text(status: dict) -> str:
    quotes = status.get("quotes_running")
    alerts = status.get("alerts_running")
    running = status.get("running")

    parts: List[str] = []
    if quotes is not None:
        parts.append(f"quotes {'运行中' if quotes else '已停止'}")
    if alerts is not None:
        parts.append(f"alerts {'运行中' if alerts else '已停止'}")
    if not parts and running is not None:
        parts.append(f"系统 {'运行中' if running else '未运行'}")
    if not parts:
        parts.append("系统运行状态未知")
    return "，".join(parts)


def _data_health_text(events: List[Tuple[dict, int]], status: dict) -> str:
    data_health = status.get("data_health") or status.get("health")
    if isinstance(data_health, str):
        return f"数据健康：{data_health}"

    recent_flags = [
        ev
        for ev, _ in events
        if str(ev.get("event_type", "")).startswith("DATA_")
    ]
    if recent_flags:
        latest = recent_flags[-1]
        summary = latest.get("message") or latest.get("event_type") or "状态未知"
        return f"数据健康：{summary}"

    return "数据健康：无显著告警"


def _load_events() -> Tuple[List[Tuple[dict, int]], Optional[Path]]:
    events_path = _latest_file("events_*.jsonl")
    if not events_path:
        return [], None
    events: List[Tuple[dict, int]] = []
    try:
        with events_path.open("r", encoding="utf-8") as fh:
            for idx, line in enumerate(fh, start=1):
                try:
                    events.append((json.loads(line), idx))
                except Exception:
                    continue
    except Exception:
        return [], events_path
    return events, events_path


def _high_priority_events(events: List[Tuple[dict, int]], events_path: Optional[Path]) -> List[Evidence]:
    result: List[Evidence] = []
    for ev, line_no in reversed(events):
        etype = ev.get("event_type", "")
        if etype == "MOVE" or etype.startswith("DATA_") or etype == "AI_ANSWER":
            ts = ev.get("ts_utc") or ev.get("ts") or "?"
            symbol = ev.get("symbol") or "-"
            message = ev.get("message") or "(no message)"
            marker = ts if events_path is None else f"{events_path.name}#L{line_no}"
            result.append(Evidence(f"{etype} {symbol}: {message}", marker))
        if len(result) >= 3:
            break
    return list(reversed(result))


def _suggest_next_step(events: List[Tuple[dict, int]]) -> str:
    has_data_issue = any(
        str(ev.get("event_type", "")).startswith("DATA_") for ev, _ in events[-5:]
    )
    if has_data_issue:
        return "建议下一步：验收（关注数据告警，确认来源后再操作）"
    if events:
        return "建议下一步：观察（持续跟踪最新事件）"
    return "建议下一步：研究（完善环境与数据）"


def _format_section(title: str, body: str) -> str:
    return f"{title}: {body}"


def generate_summary() -> str:
    status_path = _latest_file("status.json")
    status = _load_json(status_path) if status_path else {}
    events, events_path = _load_events()

    lines: List[str] = []
    lines.append(_format_section("系统是否在跑", _system_status_text(status)))
    lines.append(_format_section("数据健康", _data_health_text(events, status)))

    lines.append("最近高优先级事件：")
    important = _high_priority_events(events, events_path)
    if not important:
        lines.append("- (暂无高优先级事件)")
    else:
        for ev in important:
            lines.append(f"- {ev.format()}")

    lines.append(_suggest_next_step(events))
    return "\n".join(lines)


def main() -> int:
    summary = generate_summary()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
