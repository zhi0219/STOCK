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
from tools.write_xp_snapshot import write_xp_snapshot
from tools.paths import to_repo_relative

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts"
RUNS_ROOT = ROOT / "Logs" / "train_runs" / "_pr38_gate"
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
        ts = start + timedelta(minutes=15 * idx)
        qty = 1 if idx % 2 == 0 else -1
        rows.append(
            {
                "ts_utc": ts.isoformat(),
                "symbol": "SIM",
                "qty": qty,
                "price": 100.0 + idx,
                "pnl": -0.25 if qty > 0 else 0.75,
                "fee_usd": 0.25,
            }
        )
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _write_minimal_latest(runs_root: Path, run_id: str) -> None:
    latest_dir = runs_root / "_latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    history_path = runs_root / "promotion_history.jsonl"
    events = [
        {"ts_utc": "2024-01-01T00:00:00Z", "decision": "REJECT"},
        {"ts_utc": "2024-01-02T00:00:00Z", "decision": "REJECT"},
        {"ts_utc": "2024-01-03T00:00:00Z", "decision": "REJECT"},
    ]
    history_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    tournament = {
        "schema_version": 1,
        "created_utc": "2024-01-03T00:00:00Z",
        "run_id": run_id,
        "git_commit": "deadbeef",
        "entries": [],
    }
    judge = {
        "schema_version": 1,
        "created_utc": "2024-01-03T00:00:00Z",
        "run_id": run_id,
        "git_commit": "deadbeef",
        "scores": {"advantages": {"baseline_do_nothing": 0.0, "baseline_buy_hold": 0.0}},
        "candidate_id": "candidate_a",
    }
    promotion = {
        "schema_version": 1,
        "created_utc": "2024-01-03T00:00:00Z",
        "run_id": run_id,
        "git_commit": "deadbeef",
        "candidate_id": "candidate_a",
        "decision": "REJECT",
        "reasons": ["gate_rejected"],
        "required_next_steps": ["collect_more_runs_for_gate"],
    }
    history_latest = {
        "schema_version": 1,
        "ts_utc": "2024-01-03T00:00:00Z",
        "run_id": run_id,
        "git_commit": "deadbeef",
        "history_path": to_repo_relative(history_path),
    }
    (latest_dir / "tournament_result_latest.json").write_text(
        json.dumps(tournament, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (latest_dir / "judge_result_latest.json").write_text(
        json.dumps(judge, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (latest_dir / "promotion_decision_latest.json").write_text(
        json.dumps(promotion, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (latest_dir / "promotion_history_latest.json").write_text(
        json.dumps(history_latest, indent=2, ensure_ascii=False), encoding="utf-8"
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
    run_id = "pr38_gate_run"
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    orders_path = run_dir / "orders_sim.jsonl"
    _write_orders(orders_path)

    decision_cards = []
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for idx in range(2):
        ts = (start + timedelta(minutes=30 * idx)).isoformat()
        action = "BUY" if idx == 0 else "SELL"
        decision_cards.append(
            {
                "schema_version": 1,
                "ts_utc": ts,
                "step_id": idx,
                "episode_id": run_id,
                "symbol": "SIM",
                "action": action,
                "size": 1,
                "price_snapshot": {"last": 100 + idx, "currency": "USD"},
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

    report_path = ARTIFACTS_DIR / "trade_activity_report.json"
    report_payload = _safe_read_json(report_path)
    if not report_payload:
        errors.append("trade_activity_report_missing")
    else:
        required = [
            "schema_version",
            "created_utc",
            "run_id",
            "status",
            "trades_total",
            "turnover_gross",
            "violations",
        ]
        missing = [field for field in required if field not in report_payload]
        if missing:
            errors.append(f"trade_activity_report_missing_fields:{','.join(missing)}")
        errors.extend(_assert_repo_relative(report_payload))

    trade_latest = RUNS_ROOT / "_latest" / "trade_activity_report_latest.json"
    if not trade_latest.exists():
        errors.append("trade_activity_latest_missing")

    decision = evaluate_promotion_gate(
        {"candidate_id": "candidate_a", "score": 1.0, "max_drawdown_pct": 1.0, "turnover": 1, "reject_rate": 0.0},
        [{"candidate_id": "baseline_do_nothing", "score": 0.5}],
        run_id,
        GateConfig(require_trade_activity=True),
        stress_report={"status": "PASS", "baseline_pass": True, "stress_pass": True, "scenarios": [{"pass": True}]},
        trade_activity_report=report_payload,
    )
    if "trade_activity_status" not in decision:
        errors.append("promotion_gate_trade_activity_missing")

    _write_minimal_latest(RUNS_ROOT, run_id)
    write_xp_snapshot(runs_root=RUNS_ROOT, artifacts_output=ARTIFACTS_DIR / "xp_snapshot.json")
    xp_payload = _safe_read_json(ARTIFACTS_DIR / "xp_snapshot.json")
    if not xp_payload:
        errors.append("xp_snapshot_missing")
    else:
        source_artifacts = xp_payload.get("source_artifacts", {}) if isinstance(xp_payload.get("source_artifacts"), dict) else {}
        if "trade_activity_report" not in source_artifacts:
            errors.append("xp_snapshot_missing_trade_activity_source")

    if os.environ.get("PR38_FORCE_FAIL") == "1":
        errors.append("PR38_FORCE_FAIL")

    if errors:
        print("verify_pr38_gate FAIL")
        for err in errors:
            print(f" - {err}")
        return 1

    print("verify_pr38_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
