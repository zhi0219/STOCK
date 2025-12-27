from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_SCRIPT = ROOT / "tools" / "progress_index.py"


def _write_sample_run(base: Path) -> Path:
    run_dir = base / "2024-01-01" / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_json = run_dir / "summary.json"
    summary_json.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "policy_version": "v1",
                "start_equity": 10000.0,
                "end_equity": 10250.0,
                "net_change": 250.0,
                "max_drawdown": 1.2,
                "turnover": 12,
                "rejects_count": 0,
                "gates_triggered": [],
                "stop_reason": "completed",
                "timestamps": {"start": "2024-01-01T00:00:00+00:00", "end": "2024-01-01T00:01:00+00:00"},
                "parse_warnings": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = run_dir / "summary.md"
    summary.write_text("# Training summary\n", encoding="utf-8")
    equity = run_dir / "equity_curve.csv"
    equity.write_text(
        "ts_utc,equity_usd,cash_usd,drawdown_pct,step,policy_version,mode\n"
        "2024-01-01T00:00:00+00:00,10000,10000,0,1,v1,NORMAL\n"
        "2024-01-01T00:01:00+00:00,10250,10050,-0.01,2,v1,NORMAL\n",
        encoding="utf-8",
    )
    holdings = run_dir / "holdings.json"
    holdings.write_text(
        json.dumps(
            {
                "timestamp": "2024-01-01T00:01:00+00:00",
                "cash_usd": 10050.0,
                "positions": {"AAPL": 1.0, "MSFT": -1.0},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    run_meta = run_dir / "run_meta.json"
    run_meta.write_text(
        json.dumps(
            {
                "run_id": "run_001",
                "stop_reason": "completed",
                "steps_completed": 2,
                "trades": 2,
                "rejects": {},
                "gates_triggered": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    run_complete = run_dir / "run_complete.json"
    run_complete.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_utc": "2024-01-01T00:01:00+00:00",
                "run_id": "run_001",
                "status": "complete",
                "artifacts": {
                    "equity_curve.csv": str(equity),
                    "summary.json": str(summary_json),
                    "holdings.json": str(holdings),
                    "run_meta.json": str(run_meta),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir


def _run_progress_index(runs_root: Path, output_path: Path) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(PROGRESS_SCRIPT), "--runs-root", str(runs_root), "--output", str(output_path)]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


def run() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        runs_root = base / "train_runs"
        output_path = runs_root / "progress_index.json"
        _write_sample_run(runs_root)
        result = _run_progress_index(runs_root, output_path)

        stdout = result.stdout or ""
        if result.returncode != 0:
            print(stdout)
            print(result.stderr)
            print("FAIL: progress_index.py returned non-zero exit")
            return 1
        if "PROGRESS_INDEX_SUMMARY" not in stdout:
            print(stdout)
            print("FAIL: summary marker missing from output")
            return 1
        if not output_path.exists():
            print("FAIL: progress_index.json not written")
            return 1
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - sanity
            print(f"FAIL: could not parse progress_index.json: {exc}")
            return 1
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        if not entries:
            print("FAIL: entries missing from progress index")
            return 1
        entry = entries[0]
        holdings = entry.get("holdings_preview", [])
        equity_points = entry.get("equity_points", [])
        if not holdings or holdings[0].get("symbol") != "AAPL":
            print("FAIL: holdings preview did not load holdings.json")
            return 1
        if len(equity_points) < 2:
            print("FAIL: equity preview did not load csv rows")
            return 1
        if not entry.get("has_summary_json") or not entry.get("has_holdings_json"):
            print("FAIL: progress index flags missing summary/holdings")
            return 1
        if entry.get("parse_error"):
            print("FAIL: progress index reported parse_error for valid run")
            return 1
        print("PROGRESS_VERIFY_SUMMARY|status=PASS|entries=1|message=progress index generated")
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
