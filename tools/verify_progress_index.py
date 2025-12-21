from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.progress_index import OUTPUT_INDEX, RUNS_ROOT, build_progress_index
SYNTH_ROOT = RUNS_ROOT / "_progress_synth"


def _write_equity(path: Path, values: list[float]) -> None:
    header = "ts_utc,equity_usd,cash_usd,drawdown_pct,step,policy_version,mode\n"
    lines = [header]
    for idx, val in enumerate(values):
        lines.append(f"2024-01-01T00:{idx:02d}:00Z,{val},{max(0.0, 1000 - val):.2f},0,{idx},v1,SIM\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def _write_summary(path: Path, net: float, dd: float, turnover: float, rejects: int, gates: str) -> None:
    body = "\n".join(
        [
            "# Train Daemon Summary",
            "",
            "Run: synth",
            "Policy: v1",
            f"Stop reason: test",
            f"Net value change: {net:+.2f} USD",
            f"Max drawdown: {dd:.2f}%",
            "Trades executed: 0",
            f"Turnover: {turnover:.2f}",
            f"Reject count: {rejects}",
            f"Gates triggered: {gates}",
            "",
            "## Outputs",
            "- equity_curve.csv",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _synth_runs() -> None:
    if SYNTH_ROOT.exists():
        shutil.rmtree(SYNTH_ROOT)
    (SYNTH_ROOT / "CMD-20240101-001").mkdir(parents=True, exist_ok=True)
    (SYNTH_ROOT / "CMD-20240102-002").mkdir(parents=True, exist_ok=True)

    run_a = SYNTH_ROOT / "CMD-20240101-001" / "run_A"
    run_b = SYNTH_ROOT / "CMD-20240102-002" / "run_B"
    run_a.mkdir(parents=True, exist_ok=True)
    run_b.mkdir(parents=True, exist_ok=True)

    _write_equity(run_a / "equity_curve.csv", [1000, 1010, 1020])
    _write_summary(run_a / "summary.md", 20.0, 1.5, 0.10, 2, "none")

    _write_equity(run_b / "equity_curve.csv", [1000, 950, 970, 980])
    _write_summary(run_b / "summary.md", -20.0, 5.0, 0.30, 5, "risk_limit")


def main() -> int:
    print("VERIFY_PROGRESS_INDEX_SUMMARY|status=START")
    _synth_runs()
    index_path = OUTPUT_INDEX
    try:
        index = build_progress_index(SYNTH_ROOT, index_path)
    except Exception as exc:  # pragma: no cover - simple print path
        print(f"VERIFY_PROGRESS_INDEX_SUMMARY|status=FAIL|error={exc}")
        return 1

    if not index_path.exists():
        print("VERIFY_PROGRESS_INDEX_SUMMARY|status=FAIL|error=no index written")
        return 1

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    runs = payload.get("runs", [])
    if len(runs) != 2:
        print("VERIFY_PROGRESS_INDEX_SUMMARY|status=FAIL|error=unexpected run count")
        return 1

    latest_run = payload.get("latest_run") or {}
    best_equity = payload.get("best_equity_run") or {}
    if "20240102" not in (latest_run.get("run_dir") or ""):
        print("VERIFY_PROGRESS_INDEX_SUMMARY|status=FAIL|error=latest run mismatch")
        return 1
    if float(best_equity.get("final_equity") or 0) < 1020:
        print("VERIFY_PROGRESS_INDEX_SUMMARY|status=FAIL|error=best equity mismatch")
        return 1

    summary_line = (
        "VERIFY_PROGRESS_INDEX_SUMMARY|status=PASS|runs=2|latest="
        f"{latest_run.get('run_dir')}|best={best_equity.get('run_dir')}"
    )
    print(summary_line)
    print(summary_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
