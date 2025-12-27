from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
LOGS_DIR = ROOT / "Logs"
PROGRESS_JUDGE = TOOLS_DIR / "progress_judge.py"
REPO_HYGIENE = TOOLS_DIR / "verify_repo_hygiene.py"
PROGRESS_JUDGE_LATEST = LOGS_DIR / "train_runs" / "progress_judge" / "latest.json"
SUMMARY_TAG = "PR14_GATE_SUMMARY"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.git_baseline_probe import probe_baseline
from tools.ui_parsers import load_policy_history, load_progress_judge_latest


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_equity_curve(path: Path, equities: List[float]) -> None:
    lines = ["ts_utc,equity_usd,cash_usd,drawdown_pct,policy_version,mode"]
    for idx, value in enumerate(equities):
        lines.append(f"2024-01-01T00:00:{idx:02d}Z,{value:.2f},{value:.2f},0.0,v1,SIM")
    path.write_text("\n".join(lines), encoding="utf-8")


def _summary_payload(run_id: str, start_equity: float, end_equity: float) -> Dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_version": "v1",
        "start_equity": start_equity,
        "end_equity": end_equity,
        "net_change": end_equity - start_equity,
        "max_drawdown": 1.2,
        "turnover": 4,
        "rejects_count": 1,
        "gates_triggered": [],
        "stop_reason": "cooldown",
        "timestamps": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T00:10:00Z"},
        "parse_warnings": [],
        "run_id": run_id,
    }


def _synthesize_runs(root: Path) -> List[Path]:
    root.mkdir(parents=True, exist_ok=True)
    run_dirs = []
    for idx in range(3):
        run_dir = root / f"run_{idx}"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_dirs.append(run_dir)
        summary_md = run_dir / "summary.md"
        summary_md.write_text(
            "\n".join(
                [
                    "# SIM training summary",
                    "Stop reason: cooldown",
                    "Net value change: +1.0%",
                    "Trades executed: 4",
                    "Turnover: 4",
                    "Reject count: 1",
                    "Gates triggered: none",
                    "## Notes",
                    "- SIM-only validation run",
                ]
            ),
            encoding="utf-8",
        )
        _write_equity_curve(run_dir / "equity_curve.csv", [100.0, 101.0 + idx, 102.0 + idx])
        _write_json(run_dir / "summary.json", _summary_payload(run_dir.name, 100.0, 102.0 + idx))
        (run_dir / "holdings.json").write_text('{"positions": {}, "cash_usd": 1000}', encoding="utf-8")
        (run_dir / "orders_sim.jsonl").write_text('{"symbol":"SIM","pnl":0.0}', encoding="utf-8")
    return run_dirs


def _synthesize_policy_registry(path: Path) -> None:
    payload = {
        "current_policy_version": "v1",
        "policies": {"v1": {"policy_version": "v1", "risk_overrides": {}, "created_at": "2024-01-01T00:00:00Z"}},
        "history": [
            {
                "action": "CANDIDATE",
                "policy_version": "v1",
                "evidence": str(LOGS_DIR / "train_runs" / "run_0" / "summary.json"),
                "ts_utc": "2024-01-01T00:00:00Z",
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, payload)


def _run_progress_judge(runs_root: Path) -> tuple[int, str]:
    cmd = [sys.executable, str(PROGRESS_JUDGE), "--runs-root", str(runs_root), "--seed", "14"]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output.strip()


def _run_repo_hygiene() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(REPO_HYGIENE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output.strip()


def _summary_line(
    status: str,
    degraded: bool,
    degraded_reasons: List[str],
    baseline: str | None,
    baseline_status: str,
    baseline_details: str,
) -> str:
    detail = ";".join(degraded_reasons) if degraded_reasons else "ok"
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"degraded={int(degraded)}",
            f"degraded_reasons={detail}",
            f"baseline={baseline or 'unavailable'}",
            f"baseline_status={baseline_status}",
            f"baseline_details={baseline_details}",
        ]
    )


def main() -> int:
    status = "PASS"
    reasons: List[str] = []
    degraded_reasons: List[str] = []
    judge_output = ""
    hygiene_output = ""

    baseline_info = probe_baseline()
    baseline = baseline_info.get("baseline")
    baseline_status = baseline_info.get("status") or "UNAVAILABLE"
    baseline_details = baseline_info.get("details") or "unknown"
    if baseline_status != "AVAILABLE":
        degraded_reasons.append(f"baseline_unavailable_{baseline_details}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        runs_root = tmp_root / "train_runs"
        run_dirs = _synthesize_runs(runs_root)
        policy_registry = tmp_root / "policy_registry.json"
        _synthesize_policy_registry(policy_registry)

        judge_rc, judge_output = _run_progress_judge(runs_root)
        if judge_rc != 0:
            status = "FAIL"
            reasons.append("progress_judge_failed")

        latest_payload = load_progress_judge_latest(PROGRESS_JUDGE_LATEST)
        if latest_payload.get("schema_version") != "1.0":
            status = "FAIL"
            reasons.append("judge_schema_missing")
        if latest_payload.get("recommendation") is None:
            status = "FAIL"
            reasons.append("judge_recommendation_missing")
        evidence = latest_payload.get("evidence")
        evidence_run_ids = evidence.get("run_ids") if isinstance(evidence, dict) else []
        if run_dirs and (not isinstance(evidence_run_ids, list) or not evidence_run_ids):
            status = "FAIL"
            reasons.append("judge_evidence_missing")

        for run_dir in run_dirs:
            judge_path = run_dir / "judge.json"
            if not judge_path.exists():
                status = "FAIL"
                reasons.append(f"run_judge_missing:{run_dir.name}")

        policy_entries = load_policy_history(policy_registry)
        if not isinstance(policy_entries, list):
            status = "FAIL"
            reasons.append("policy_history_parse_failed")

    hygiene_rc, hygiene_output = _run_repo_hygiene()
    if hygiene_rc != 0:
        status = "FAIL"
        reasons.append("repo_hygiene_failed")

    if reasons:
        status = "FAIL"

    degraded = bool(degraded_reasons)
    summary = _summary_line(status, degraded, degraded_reasons, baseline, baseline_status, baseline_details)

    print("PR14_GATE_START")
    print(summary)
    if judge_output:
        print(judge_output)
    if hygiene_output:
        print(hygiene_output)
    if reasons:
        print("REASONS:")
        for reason in reasons:
            print(f"- {reason}")
    if degraded_reasons:
        print("DEGRADED:")
        for reason in degraded_reasons:
            print(f"- {reason}")
    print(summary)
    print("PR14_GATE_END")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
