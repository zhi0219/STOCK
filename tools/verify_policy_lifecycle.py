from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
LOGS = ROOT / "Logs"
PYTHON = Path(".\\.venv\\Scripts\\python.exe") if Path(".\\.venv\\Scripts\\python.exe").exists() else Path(sys.executable)


def _make_quotes(path: Path) -> None:
    ts_base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for idx in range(4):
        payload = {
            "ts_utc": (ts_base + timedelta(days=idx)).isoformat(),
            "symbol": "LIFECYCLE",
            "price": str(100 + idx * 3),
        }
        if idx == 2:
            payload["data_flags"] = "DATA_STALE"
        rows.append(payload)
    header = ["ts_utc", "symbol", "price", "data_flags"]
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(str(row.get(col, "")) for col in header))
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_events(run_dir: Path) -> Path:
    events_path = run_dir / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    proposal = {
        "event_type": "GUARD_PROPOSAL",
        "message": "drawdown spike; tighten",
        "proposal": "Reduce drawdown and slow down",
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }
    events_path.write_text(json.dumps(proposal) + "\n", encoding="utf-8")
    return events_path


def _cleanup(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            if path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        quotes_path = tmpdir / "quotes.csv"
        _make_quotes(quotes_path)
        run_dir = LOGS / "tournament_runs" / "synthetic_run"
        _cleanup([LOGS / "policy_registry.json", LOGS / "policy_candidate.json", LOGS / "tournament_runs", LOGS / "Reports"])
        events_path = _write_events(run_dir)

        cmd_candidate = [str(PYTHON), str(TOOLS / "policy_candidate.py"), "--events", str(events_path)]
        if os.spawnv(os.P_WAIT, cmd_candidate[0], cmd_candidate) != 0:
            raise AssertionError("policy_candidate failed")

        registry_path = LOGS / "policy_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert "candidate" in "".join(registry.get("policies", {}).keys())

        cmd_promotion = [
            str(PYTHON),
            str(TOOLS / "verify_policy_promotion.py"),
            "--input",
            str(quotes_path),
            "--windows",
            "2026-01-01..2026-01-04",
            "--variants",
            "baseline",
            "--max-steps",
            "8",
        ]
        result = os.spawnv(os.P_WAIT, cmd_promotion[0], cmd_promotion)
        assert result == 0 or result == 1, "Promotion script did not run"

        updated_registry = json.loads(registry_path.read_text(encoding="utf-8"))
        history = updated_registry.get("history", [])
        assert history, "History should record promotion or rejection"
        assert any(item.get("policy_version", "").startswith("candidate-") for item in history)

        summary_files = list((LOGS / "tournament_runs").glob("tournament_summary_*.json"))
        assert summary_files, "Tournament summaries missing"

    print("verify_policy_lifecycle PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
