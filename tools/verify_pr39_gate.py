from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.promotion_gate_v2 import GateConfig, evaluate_promotion_gate
from tools.replay_artifacts import write_replay_artifacts
from tools.paths import to_repo_relative

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts"
RUNS_ROOT = ROOT / "Logs" / "train_runs" / "_pr39_gate"
ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\")


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _contains_absolute_path(text: str) -> bool:
    if not text:
        return False
    if text.startswith("/"):
        return True
    if ABS_PATH_PATTERN.search(text):
        return True
    if re.match(r"^[A-Za-z]:", text):
        return True
    if "\\Users\\" in text:
        return True
    if "/home/runner/" in text or "/workspace/" in text:
        return True
    return False


def _collect_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _collect_strings(key)
            yield from _collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_strings(item)
    elif isinstance(value, str):
        yield value


def _assert_repo_relative(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for text in _collect_strings(payload):
        if _contains_absolute_path(text):
            errors.append(f"absolute_path_detected:{text}")
    return errors


def _write_orders(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for idx in range(4):
        ts = start + timedelta(minutes=20 * idx)
        qty = 1 if idx % 2 == 0 else -1
        rows.append(
            {
                "ts_utc": ts.isoformat(),
                "symbol": "SIM",
                "qty": qty,
                "price": 100.0 + idx,
                "pnl": -0.1 if qty > 0 else 0.3,
                "fee_usd": 0.2,
            }
        )
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _write_run_complete(run_dir: Path, run_id: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "summary.json"
    holdings_path = run_dir / "holdings.json"
    equity_path = run_dir / "equity_curve.csv"
    run_meta_path = run_dir / "run_meta.json"
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "policy_version": "v1",
                "start_equity": 10000.0,
                "end_equity": 10050.0,
                "net_change": 50.0,
                "max_drawdown": 0.5,
                "turnover": 1,
                "rejects_count": 0,
                "gates_triggered": [],
                "stop_reason": "completed",
                "timestamps": {"start": "2024-01-01T00:00:00+00:00", "end": "2024-01-01T00:01:00+00:00"},
                "parse_warnings": [],
                "run_id": run_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
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
                "run_id": run_id,
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
                "run_id": run_id,
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


def _run_command(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(block for block in [result.stdout, result.stderr] if block)
    return result.returncode, output


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "pr39_gate_run"
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_run_complete(run_dir, run_id)
    orders_path = run_dir / "orders_sim.jsonl"
    _write_orders(orders_path)

    decision_cards = []
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for idx in range(60):
        ts = (start + timedelta(minutes=5 * idx)).isoformat()
        action = "BUY" if idx % 2 == 0 else "SELL"
        decision_cards.append(
            {
                "schema_version": 1,
                "ts_utc": ts,
                "step_id": idx,
                "episode_id": run_id,
                "symbol": "SIM",
                "action": action,
                "size": 1,
                "price_snapshot": {"last": 100 + idx * 0.5, "currency": "USD"},
                "signals": [{"name": "intent", "value": action}],
                "guards": {
                    "kill_switch": False,
                    "data_health": "PASS",
                    "cooldown_ok": True,
                    "limits_ok": True,
                    "no_lookahead_ok": True,
                    "walk_forward_window_id": None,
                },
                "decision": {"accepted": True, "reject_reason_codes": []},
                "evidence": {"paths": [to_repo_relative(orders_path)]},
                "pnl_delta": 0.0,
            }
        )

    write_replay_artifacts(run_dir, run_id, "deadbeef", decision_cards)
    replay_latest = run_dir / "_latest" / "replay_index_latest.json"
    if not replay_latest.exists():
        errors.append("replay_index_latest_missing")

    calibrate_cmd = [
        sys.executable,
        "-m",
        "tools.overtrading_calibrate",
        "--runs-root",
        str(RUNS_ROOT),
        "--min-samples",
        "1",
        "--artifacts-output",
        str(ARTIFACTS_DIR / "overtrading_calibration.json"),
        "--latest-output",
        str(ROOT / "Logs" / "train_runs" / "_latest" / "overtrading_calibration_latest.json"),
    ]
    rc, output = _run_command(calibrate_cmd)
    if rc != 0:
        errors.append("overtrading_calibration_failed")
    if "OVERTRADING_CALIBRATE_SUMMARY" not in output:
        errors.append("overtrading_calibration_marker_missing")

    audit_cmd = [
        sys.executable,
        "-m",
        "tools.trade_activity_audit",
        "--replay-index",
        str(replay_latest),
        "--run-dir",
        str(run_dir),
        "--artifacts-output",
        str(ARTIFACTS_DIR / "trade_activity_report.json"),
    ]
    rc, output = _run_command(audit_cmd)
    if rc != 0:
        errors.append("trade_activity_audit_failed")
    if "TRADE_ACTIVITY_AUDIT_START" not in output:
        errors.append("trade_activity_audit_marker_missing")

    regime_cmd = [
        sys.executable,
        "-m",
        "tools.regime_classifier",
        "--run-dir",
        str(run_dir),
        "--artifacts-output",
        str(ARTIFACTS_DIR / "regime_report.json"),
    ]
    rc, output = _run_command(regime_cmd)
    if rc != 0:
        errors.append("regime_classifier_failed")
    if "REGIME_CLASSIFIER_SUMMARY" not in output:
        errors.append("regime_classifier_marker_missing")

    calibration_path = ARTIFACTS_DIR / "overtrading_calibration.json"
    calibration_payload = _safe_read_json(calibration_path)
    if not calibration_payload:
        errors.append("overtrading_calibration_missing")
    else:
        required = ["schema_version", "created_utc", "status", "regimes", "sample_size", "run_ids"]
        missing = [field for field in required if field not in calibration_payload]
        if missing:
            errors.append(f"overtrading_calibration_missing_fields:{','.join(missing)}")
        errors.extend(_assert_repo_relative(calibration_payload))
    calibration_latest = ROOT / "Logs" / "train_runs" / "_latest" / "overtrading_calibration_latest.json"
    if not calibration_latest.exists():
        errors.append("overtrading_calibration_latest_missing")

    regime_path = ARTIFACTS_DIR / "regime_report.json"
    regime_payload = _safe_read_json(regime_path)
    if not regime_payload:
        errors.append("regime_report_missing")
    else:
        required = ["schema_version", "created_utc", "label", "status", "metrics"]
        missing = [field for field in required if field not in regime_payload]
        if missing:
            errors.append(f"regime_report_missing_fields:{','.join(missing)}")
        errors.extend(_assert_repo_relative(regime_payload))
    regime_latest = run_dir / "_latest" / "regime_report_latest.json"
    if not regime_latest.exists():
        errors.append("regime_report_latest_missing")

    trade_path = ARTIFACTS_DIR / "trade_activity_report.json"
    trade_payload = _safe_read_json(trade_path)
    if not trade_payload:
        errors.append("trade_activity_report_missing")
    else:
        if "regime" not in trade_payload:
            errors.append("trade_activity_report_missing_regime")
        if "calibration" not in trade_payload:
            errors.append("trade_activity_report_missing_calibration")
        errors.extend(_assert_repo_relative(trade_payload))

    decision = evaluate_promotion_gate(
        {"candidate_id": "candidate_a", "score": 1.0, "max_drawdown_pct": 1.0, "turnover": 1, "reject_rate": 0.0},
        [{"candidate_id": "baseline_do_nothing", "score": 0.5}],
        run_id,
        GateConfig(require_trade_activity=True, require_overtrading_calibration=True),
        stress_report={"status": "PASS", "baseline_pass": True, "stress_pass": True, "scenarios": [{"pass": True}]},
        trade_activity_report=trade_payload,
    )
    if "trade_activity_calibration_status" not in decision:
        errors.append("promotion_gate_missing_calibration_status")

    missing_calibration_report = dict(trade_payload or {}) if isinstance(trade_payload, dict) else {}
    missing_calibration_report["calibration"] = {}
    decision_missing = evaluate_promotion_gate(
        {"candidate_id": "candidate_a", "score": 1.0, "max_drawdown_pct": 1.0, "turnover": 1, "reject_rate": 0.0},
        [{"candidate_id": "baseline_do_nothing", "score": 0.5}],
        run_id,
        GateConfig(require_trade_activity=True, require_overtrading_calibration=True),
        stress_report={"status": "PASS", "baseline_pass": True, "stress_pass": True, "scenarios": [{"pass": True}]},
        trade_activity_report=missing_calibration_report,
    )
    if decision_missing.get("decision") != "REJECT":
        errors.append("promotion_gate_not_fail_closed")
    reasons = decision_missing.get("reasons", [])
    if isinstance(reasons, list) and "overtrading_calibration_missing" not in reasons:
        errors.append("promotion_gate_missing_fail_reason")

    if os.environ.get("PR39_FORCE_FAIL") == "1":
        errors.append("PR39_FORCE_FAIL")

    if errors:
        print("verify_pr39_gate FAIL")
        for err in errors:
            print(f" - {err}")
        return 1

    print("verify_pr39_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
