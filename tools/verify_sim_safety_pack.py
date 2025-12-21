from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.sim_autopilot import SimAutopilot

UTC = timezone.utc


def _require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}")
        sys.exit(1)


def _new_autopilot(tmpdir: Path, index: int, overrides: dict | None = None) -> SimAutopilot:
    logs_dir = tmpdir / f"scenario_{index}" / "Logs"
    return SimAutopilot(logs_dir=logs_dir, risk_overrides=overrides or {})


def _read_jsonl(path: Path) -> list[dict]:
    results: list[dict] = []
    if not path.exists():
        return results
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            results.append(json.loads(line))
    return results


def scenario_data_stale(tmpdir: Path) -> None:
    autopilot = _new_autopilot(tmpdir, 1)
    status = {"data_status": "DATA_STALE"}
    decision, reason = autopilot.process_intent(
        {"symbol": "AAPL", "qty": 1, "price": 10.0}, status=status, now_ts=datetime(2024, 1, 1, tzinfo=UTC)
    )
    _require(decision == "RISK_REJECT", f"expected RISK_REJECT, got {decision} ({reason})")
    _require(not autopilot.orders_path.exists(), "orders_sim.jsonl should not be written on data reject")


def scenario_high_frequency(tmpdir: Path) -> None:
    autopilot = _new_autopilot(tmpdir, 2, {"max_orders_per_minute": 1, "min_interval_seconds": 60})
    base_ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    first = autopilot.process_intent({"symbol": "MSFT", "qty": 1, "price": 5.0}, now_ts=base_ts)
    _require(first[0] == "ALLOW", f"first intent should pass, got {first}")
    second = autopilot.process_intent({"symbol": "MSFT", "qty": 1, "price": 5.0}, now_ts=base_ts + timedelta(seconds=5))
    _require(second[0] == "RISK_REJECT", "second intent should hit rate limit")


def scenario_postmortem(tmpdir: Path) -> None:
    autopilot = _new_autopilot(tmpdir, 3, {"max_daily_loss": 10.0, "max_drawdown": 0.01})
    decision, _ = autopilot.process_intent(
        {"symbol": "NVDA", "qty": 1, "price": 20.0, "pnl": -12.0},
        status={"data_status": "HEALTHY"},
        now_ts=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
    )
    _require(decision == "ALLOW", "loss breach happens after first fill")
    events = _read_jsonl(autopilot.events_path)
    postmortem_events = [e for e in events if e.get("event_type") == "POSTMORTEM"]
    _require(postmortem_events, "POSTMORTEM event should be recorded")
    final_state = json.loads((autopilot.logs_dir / "risk_state.json").read_text(encoding="utf-8"))
    _require(final_state.get("mode") in {"SAFE", "OBSERVE"}, "mode should degrade after loss breach")
    evidence = postmortem_events[0].get("evidence", "")
    _require("orders_sim.jsonl#L" in evidence, "postmortem evidence must reference orders_sim line")


def scenario_orders_schema(tmpdir: Path) -> None:
    autopilot = _new_autopilot(tmpdir, 4)
    autopilot.process_intent({"symbol": "SPY", "qty": 2, "price": 1.5}, now_ts=datetime(2024, 1, 1, 15, 0, tzinfo=UTC))
    lines = _read_jsonl(autopilot.orders_path)
    _require(lines, "orders_sim.jsonl should have at least one record")
    for idx, row in enumerate(lines, start=1):
        _require(isinstance(row, dict), f"line {idx} not JSON object")
        _require("sim_fill" in row, f"line {idx} missing sim_fill")
        _require("latency_sec" in row["sim_fill"], f"line {idx} sim_fill missing latency_sec")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        scenario_data_stale(base)
        scenario_high_frequency(base)
        scenario_postmortem(base)
        scenario_orders_schema(base)
    print("PASS: sim safety pack verifications")
    return 0


if __name__ == "__main__":
    sys.exit(main())
