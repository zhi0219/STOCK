from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import explain_now

LOGS_DIR = ROOT / "Logs"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_status(path: Path) -> str | None:
    backup = None
    if path.exists():
        backup = path.read_text(encoding="utf-8")
    status = {
        "ts_utc": _iso(datetime.now(timezone.utc)),
        "quotes_running": True,
        "alerts_running": False,
        "data_health": "正常",
    }
    path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    return backup


def _write_events(path: Path) -> None:
    now = datetime.now(timezone.utc)
    events = [
        {
            "event_type": "MOVE",
            "symbol": "SYN",
            "message": "价格快速波动",
            "ts_utc": _iso(now),
        },
        {
            "event_type": "DATA_STALE",
            "symbol": "SYN",
            "message": "数据延迟 5 分钟",
            "ts_utc": _iso(now - timedelta(minutes=1)),
        },
        {
            "event_type": "AI_ANSWER",
            "symbol": "SYN",
            "message": "回答已记录",
            "ts_utc": _iso(now - timedelta(minutes=2)),
        },
    ]
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def run() -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    status_path = LOGS_DIR / "status.json"
    events_path = LOGS_DIR / f"events_{datetime.now():%Y%m%d%H%M%S}_explain.jsonl"

    backup = _write_status(status_path)
    _write_events(events_path)

    try:
        summary = explain_now.generate_summary()
    finally:
        if backup is None:
            status_path.unlink(missing_ok=True)
        else:
            status_path.write_text(backup, encoding="utf-8")
        events_path.unlink(missing_ok=True)

    required_phrases = [
        "系统是否在跑",
        "数据健康",
        "最近高优先级事件",
        "建议下一步",
    ]
    missing = [p for p in required_phrases if p not in summary]
    if missing:
        print(f"FAIL: summary missing sections: {', '.join(missing)}")
        return 1

    if "evidence:" not in summary:
        print("FAIL: summary lacks evidence markers")
        return 1

    print("PASS: explain_now summary structure validated")
    return 0


if __name__ == "__main__":
    sys.exit(run())
