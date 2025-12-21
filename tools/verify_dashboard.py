from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dashboard_model import (  # noqa: E402
    compute_event_rows,
    compute_health,
    compute_move_leaderboard,
    load_latest_status,
    load_recent_events,
)
from tools.stdio_utf8 import configure_stdio_utf8  # noqa: E402


UTC = timezone.utc


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _write_logs(logs_dir: Path) -> Path:
    now = datetime.now(UTC)
    events_path = logs_dir / f"events_{now:%Y-%m-%d}_dashboard.jsonl"
    events: List[Dict[str, Any]] = [
        {
            "event_type": "ALERTS_START",
            "message": "alerts boot",
            "ts_utc": _iso(now - timedelta(minutes=20)),
            "metrics": {"cooldown_seconds": 120},
        },
        {
            "event_type": "MOVE",
            "symbol": "AAPL",
            "severity": "medium",
            "message": "move event",
            "ts_utc": _iso(now - timedelta(minutes=10)),
            "metrics": {"move_pct": 2.5, "threshold": 1.0},
        },
        {
            "event_type": "DATA_STALE",
            "symbol": "AAPL",
            "severity": "high",
            "message": "stale",
            "ts_utc": _iso(now - timedelta(minutes=8)),
            "metrics": {"age_sec": 400},
        },
        {
            "event_type": "DATA_FLAT",
            "symbol": "AAPL",
            "severity": "low",
            "message": "flat",
            "ts_utc": _iso(now - timedelta(minutes=5)),
            "metrics": {"run_len": 3, "threshold": 3},
        },
        {
            "event_type": "MOVE",
            "symbol": "MSFT",
            "severity": "medium",
            "message": "msft move",
            "ts_utc": _iso(now - timedelta(minutes=3)),
            "metrics": {"move_pct": -3.8, "threshold": 1.0},
        },
        {
            "event_type": "AI_ANSWER",
            "symbol": "AAPL",
            "message": "answer",
            "ts_utc": _iso(now - timedelta(minutes=1)),
        },
    ]
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")

    status_path = logs_dir / "status.json"
    status_payload = {
        "ts_utc": _iso(now - timedelta(minutes=1)),
        "config": {
            "poll_seconds": 60,
            "stale_seconds": 180,
            "flat_repeats": 3,
            "cooldown_seconds": 90,
            "watchlist": ["AAPL", "MSFT", "TSLA"],
        },
        "quotes": {"path": str(ROOT / "Data" / "quotes.csv"), "file_age_s": 30},
    }
    status_path.write_text(json.dumps(status_payload), encoding="utf-8")

    supervisor_state = {
        "ts_utc": _iso(now),
        "sources": {"quotes": {"running": True}, "alerts": {"running": True}},
    }
    (logs_dir / "supervisor_state.json").write_text(json.dumps(supervisor_state), encoding="utf-8")
    return events_path


def main() -> int:
    configure_stdio_utf8()
    tmp_dir = Path(tempfile.mkdtemp(prefix="dashboard_logs_"))
    try:
        events_path = _write_logs(tmp_dir)
        events = load_recent_events(tmp_dir, since_minutes=120)
        if len(events) < 5:
            print("FAIL: events not loaded correctly")
            return 1

        status = load_latest_status(tmp_dir)
        supervisor_state_path = tmp_dir / "supervisor_state.json"
        supervisor_state = json.loads(supervisor_state_path.read_text(encoding="utf-8"))

        health = compute_health(status, events, supervisor_state)
        lights = health.get("lights", {})
        if lights.get("data_fresh", {}).get("status") != "green":
            print("FAIL: data_fresh light not green")
            return 1
        if lights.get("data_flat", {}).get("status") != "yellow":
            print("FAIL: data_flat light expected yellow due to run_len")
            return 1
        cards = {card.get("label"): card for card in health.get("cards", [])}
        if cards.get("MOVE events (60m)", {}).get("value") != 2:
            print("FAIL: MOVE count mismatch")
            return 1
        if cards.get("DATA_* (60m)", {}).get("value") != 2:
            print("FAIL: DATA_* count mismatch")
            return 1
        if cards.get("Last AI_ANSWER", {}).get("value") == "none":
            print("FAIL: AI_ANSWER timestamp missing")
            return 1

        rows = compute_event_rows(events)
        required_fields = {"ts_et", "event_type", "symbol", "severity"}
        if any(field not in rows[0] for field in required_fields):
            print("FAIL: event rows missing required field")
            return 1

        leaderboard = compute_move_leaderboard(events)
        if not leaderboard or leaderboard[0].get("symbol") != "MSFT":
            print("FAIL: leaderboard ordering incorrect")
            return 1
        if leaderboard[0].get("max_abs_move_60m") != 3.8:
            print("FAIL: leaderboard max_abs_move_60m incorrect")
            return 1

        print("PASS: dashboard model verified", events_path)
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
