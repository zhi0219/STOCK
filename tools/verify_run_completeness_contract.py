from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.progress_index import build_progress_index

SUMMARY_TAG = "RUN_COMPLETENESS_SUMMARY"


def _summary_payload(run_id: str, start_equity: float, end_equity: float) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_version": "v1",
        "start_equity": start_equity,
        "end_equity": end_equity,
        "net_change": end_equity - start_equity,
        "max_drawdown": 0.5,
        "turnover": 1,
        "rejects_count": 0,
        "gates_triggered": [],
        "stop_reason": "completed",
        "timestamps": {"start": "2024-01-01T00:00:00+00:00", "end": "2024-01-01T00:01:00+00:00"},
        "parse_warnings": [],
        "run_id": run_id,
    }


def _write_complete_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "summary.json"
    holdings_path = run_dir / "holdings.json"
    equity_path = run_dir / "equity_curve.csv"
    run_meta_path = run_dir / "run_meta.json"
    summary_path.write_text(json.dumps(_summary_payload(run_dir.name, 10000.0, 10050.0), indent=2), encoding="utf-8")
    holdings_path.write_text(
        json.dumps({"timestamp": "2024-01-01T00:01:00+00:00", "cash_usd": 10050.0, "positions": {}}, indent=2),
        encoding="utf-8",
    )
    equity_path.write_text(
        "ts_utc,equity_usd,cash_usd,drawdown_pct,step,policy_version,mode\n"
        "2024-01-01T00:00:00+00:00,10000,10000,0,1,v1,SIM\n"
        "2024-01-01T00:01:00+00:00,10050,10050,0,2,v1,SIM\n",
        encoding="utf-8",
    )
    run_meta_path.write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "stop_reason": "completed",
                "steps_completed": 2,
                "trades": 0,
                "rejects": {},
                "gates_triggered": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    run_complete_path = run_dir / "run_complete.json"
    run_complete_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_utc": "2024-01-01T00:01:00+00:00",
                "run_id": run_dir.name,
                "status": "complete",
                "artifacts": {
                    "equity_curve.csv": str(equity_path),
                    "summary.json": str(summary_path),
                    "holdings.json": str(holdings_path),
                    "run_meta.json": str(run_meta_path),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_incomplete_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "summary.json"
    run_meta_path = run_dir / "run_meta.json"
    summary_path.write_text(json.dumps(_summary_payload(run_dir.name, 10000.0, 9990.0), indent=2), encoding="utf-8")
    run_meta_path.write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "stop_reason": "incomplete",
                "steps_completed": 0,
                "trades": 0,
                "rejects": {},
                "gates_triggered": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    print("RUN_COMPLETENESS_START")
    status = "PASS"
    issues: list[str] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        runs_root = Path(tmpdir) / "train_runs"
        complete_run = runs_root / "2024-01-01" / "run_complete"
        incomplete_run = runs_root / "2024-01-01" / "run_incomplete"
        _write_complete_run(complete_run)
        _write_incomplete_run(incomplete_run)

        payload = build_progress_index(runs_root, max_runs=10)
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        by_id = {entry.get("run_id"): entry for entry in entries if isinstance(entry, dict)}

        complete_entry = by_id.get("run_complete")
        incomplete_entry = by_id.get("run_incomplete")
        if not complete_entry:
            status = "FAIL"
            issues.append("complete_entry_missing")
        else:
            if complete_entry.get("run_complete") is not True:
                status = "FAIL"
                issues.append("complete_run_not_marked_complete")
            if not complete_entry.get("summary"):
                status = "FAIL"
                issues.append("complete_run_summary_missing")
            if not complete_entry.get("equity_stats"):
                status = "FAIL"
                issues.append("complete_run_equity_missing")

        if not incomplete_entry:
            status = "FAIL"
            issues.append("incomplete_entry_missing")
        else:
            if incomplete_entry.get("run_complete") is not False:
                status = "FAIL"
                issues.append("incomplete_run_marked_complete")
            if incomplete_entry.get("summary"):
                status = "FAIL"
                issues.append("incomplete_run_summary_present")
            missing_reason = str(incomplete_entry.get("missing_reason") or "")
            if "run_complete_missing" not in missing_reason:
                status = "FAIL"
                issues.append("missing_reason_run_complete_missing")

    summary = "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"issues={','.join(issues) if issues else 'none'}",
        ]
    )
    print(summary)
    print("RUN_COMPLETENESS_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
