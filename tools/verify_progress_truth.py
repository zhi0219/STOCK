from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_JUDGE = ROOT / "tools" / "progress_judge.py"
SUMMARY_TAG = "PROGRESS_TRUTH_SUMMARY"


def _seed_runs(base: Path) -> Path:
    runs_root = base / "train_runs"
    run_dir = runs_root / "2024-02-02" / "run_011"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = run_dir / "summary.md"
    summary.write_text(
        "\n".join(
            [
                "# SIM training summary",
                "Stop reason: completed",
                "Net value change: +1.23%",
                "Trades executed: 4",
                "Turnover: 8",
                "Reject count: 0",
                "Gates triggered: none",
                "## Notes",
                "- SIM-only validation run",
            ]
        ),
        encoding="utf-8",
    )

    equity = run_dir / "equity_curve.csv"
    equity.write_text(
        "ts_utc,equity_usd,cash_usd,drawdown_pct,step,policy_version,mode\n"
        "2024-02-02T00:00:00+00:00,10000,10000,0,1,v1,SIM\n"
        "2024-02-02T00:05:00+00:00,10123,10010,-0.01,2,v1,SIM\n",
        encoding="utf-8",
    )

    summary_json = run_dir / "summary.json"
    summary_json.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "policy_version": "v1",
                "start_equity": 10000.0,
                "end_equity": 10123.0,
                "net_change": 123.0,
                "max_drawdown": 0.1,
                "turnover": 4,
                "rejects_count": 0,
                "gates_triggered": [],
                "stop_reason": "completed",
                "timestamps": {"start": "2024-02-02T00:00:00Z", "end": "2024-02-02T00:05:00Z"},
                "parse_warnings": [],
                "run_id": "run_011",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    orders = run_dir / "orders_sim.jsonl"
    orders.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "SIM", "pnl": 1.0}),
                json.dumps({"symbol": "SAFE", "pnl": 0.0}),
            ]
        ),
        encoding="utf-8",
    )
    return runs_root


def _render_summary(status: str, reason: str, judge_rc: int) -> str:
    detail = reason or "ok"
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"judge_rc={judge_rc}",
            f"reason={detail}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        runs_root = _seed_runs(base)

        cmd = [sys.executable, str(PROGRESS_JUDGE), "--runs-root", str(runs_root)]
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        output = (result.stdout or "").strip()
        markers = [line for line in output.splitlines() if line.startswith("PROGRESS_JUDGE_SUMMARY|")]

        reason: str = ""
        status = "PASS"
        if result.returncode != 0:
            status = "FAIL"
            reason = f"judge_failed_exit_{result.returncode}"
        elif len(markers) < 2 or markers[0] != markers[-1]:
            status = "FAIL"
            reason = "summary_marker_missing_or_mismatched"
        elif "issues=0" not in markers[0]:
            status = "FAIL"
            reason = "judge_reported_issues"

        summary = _render_summary(status, reason, result.returncode)
        print(summary)
        if output:
            print(output)
        if result.stderr:
            print(result.stderr.strip())
        print(summary)
        return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
