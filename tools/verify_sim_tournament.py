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
PYTHON = Path(".\\.venv\\Scripts\\python.exe")


def _make_quotes(path: Path) -> None:
    ts_base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [
        {"ts_utc": (ts_base + timedelta(days=i)).isoformat(), "symbol": "TEST", "price": str(100 + i * 2)}
        for i in range(6)
    ]
    # Inject stale data flag to trigger risk rejection
    rows[3]["data_flags"] = "DATA_STALE"
    rows[4]["data_flags"] = "DATA_STALE"
    header = ["ts_utc", "symbol", "price", "data_flags"]
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(str(row.get(col, "")) for col in header))
    path.write_text("\n".join(lines), encoding="utf-8")


def _run_tournament(tmpdir: Path) -> Path:
    quotes_path = tmpdir / "quotes.csv"
    _make_quotes(quotes_path)
    report_dir = ROOT / "Reports"
    runs_dir = ROOT / "Logs" / "tournament_runs"
    summary_glob = runs_dir / "tournament_summary_*.json"
    for target in [report_dir, runs_dir]:
        if target.exists():
            if target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
    python_bin = PYTHON if PYTHON.exists() else Path(sys.executable)
    cmd = [
        str(python_bin),
        str(TOOLS / "sim_tournament.py"),
        "--input",
        str(quotes_path),
        "--windows",
        "2025-01-01..2025-01-03,2025-01-04..2025-01-06",
        "--variants",
        "baseline,conservative",
        "--max-steps",
        "10",
    ]
    result = os.spawnv(os.P_WAIT, cmd[0], cmd)
    if result != 0:
        raise AssertionError(f"sim_tournament failed with code {result}")
    summaries = sorted(runs_dir.glob(summary_glob.name))
    assert summaries, "Missing tournament summaries"
    return summaries[-1]


def _ensure_monotonic_equity(run_dir: Path) -> None:
    eq_path = run_dir / "equity_curve.jsonl"
    assert eq_path.exists(), f"Missing equity curve for {run_dir}"
    last_ts = None
    with eq_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            payload = json.loads(line)
            ts_val = payload["ts_utc"]
            if last_ts and ts_val < last_ts:
                raise AssertionError(f"Non-monotonic ts in {eq_path}")
            last_ts = ts_val


def _contains_reject_or_postmortem(run_dir: Path) -> bool:
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    return "RISK_REJECT" in events or "POSTMORTEM" in events


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        summary_path = _run_tournament(tmpdir)
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        runs = data.get("runs", [])
        assert len(runs) == 4, f"Expected 4 runs, got {len(runs)}"

        risk_hits = 0
        for run in runs:
            run_dir = ROOT / "Logs" / "tournament_runs" / run["run_id"]
            _ensure_monotonic_equity(run_dir)
            if _contains_reject_or_postmortem(run_dir):
                risk_hits += 1
        assert risk_hits >= 1, "Expected at least one risk reject or postmortem"

        report_path = max((ROOT / "Reports").glob("tournament_*.md"), default=None)
        assert report_path and report_path.exists(), "Report markdown missing"
        content = report_path.read_text(encoding="utf-8")
        assert "Top performers" in content and "Worst cases" in content, "Report sections missing"

        # idempotent clean + rerun
        for path in [ROOT / "Logs" / "tournament_runs", ROOT / "Reports"]:
            if path.exists():
                shutil.rmtree(path)
        summary_path = _run_tournament(tmpdir)
        assert summary_path.exists(), "Summary missing after rerun"
    print("verify_sim_tournament PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
