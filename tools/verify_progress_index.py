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
    summary = run_dir / "summary.md"
    summary.write_text(
        "\n".join(
            [
                "# Training summary",
                "Stop reason: completed",
                "Net value change: +2.5%",
                "Max drawdown: -1.2%",
                "Trades executed: 3",
                "Turnover: 12",
                "Reject count: 0",
                "Gates triggered: none",
                "## Rejection reasons",
                "- none",
            ]
        ),
        encoding="utf-8",
    )
    equity = run_dir / "equity_curve.csv"
    equity.write_text(
        "ts_utc,equity_usd,cash_usd,drawdown_pct,step,policy_version,mode\n"
        "2024-01-01T00:00:00+00:00,10000,10000,0,1,v1,NORMAL\n"
        "2024-01-01T00:01:00+00:00,10250,10050,-0.01,2,v1,NORMAL\n",
        encoding="utf-8",
    )
    orders = run_dir / "orders_sim.jsonl"
    orders.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "AAPL", "pnl": 10}),
                json.dumps({"symbol": "MSFT", "pnl": -2}),
                json.dumps({"symbol": "AAPL", "pnl": 5}),
            ]
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
            print("FAIL: holdings preview did not aggregate symbols")
            return 1
        if len(equity_points) < 2:
            print("FAIL: equity preview did not load csv rows")
            return 1
        print("PROGRESS_VERIFY_SUMMARY|status=PASS|entries=1|message=progress index generated")
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
