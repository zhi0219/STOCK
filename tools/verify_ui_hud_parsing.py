from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stdio_utf8 import configure_stdio_utf8
from tools.train_service_hud import compute_training_hud
from tools.wakeup_dashboard import MISSING_FIELD_TEXT, parse_summary_key_fields


def _write_synthetic_summary(path: Path) -> None:
    content = "\n".join(
        [
            "# Training Summary (synthetic)",
            "Stop reason: synthetic_safe_stop",
            "Net value change: +1.2%",
            "Max drawdown: -0.9%",
            "Trades executed: 4",
            "Turnover: 12%",
            "Reject count: 2",
            "Gates triggered: risk_limit, exposure_gate",
            "",
            "## Rejection reasons",
            "- cooldown_gate",
            "- risk_limit",
            "- exposure_gate",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_synthetic_state(path: Path, run_dir: Path, summary_path: Path) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "service_start_ts": (now - timedelta(hours=1)).isoformat(),
        "last_episode_end_ts": (now - timedelta(minutes=5)).isoformat(),
        "last_episode_start_ts": (now - timedelta(minutes=10)).isoformat(),
        "episodes_completed": 7,
        "last_error": None,
        "last_run_dir": str(run_dir),
        "last_summary_path": str(summary_path),
        "service_pid": 99999,
        "last_heartbeat_ts": now.isoformat(),
        "stop_reason": None,
        "next_iteration_eta": (now + timedelta(seconds=42)).isoformat(),
        "config": {
            "episode_seconds": 300,
            "cooldown_seconds_between_episodes": 12,
            "max_episodes_per_hour": 6,
            "max_episodes_per_day": 100,
            "max_total_train_runs_mb": 5000,
            "retain_days": 7,
            "retain_latest_n": 50,
            "runs_root": str(run_dir.parent.parent),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run() -> int:
    configure_stdio_utf8()
    print("=== VERIFY_UI_HUD_PARSING START ===")
    errors: list[str] = []

    service_root = ROOT / "Logs" / "train_service"
    runs_root = ROOT / "Logs" / "train_runs" / "hud_synthetic"
    run_dir = runs_root / "episode_0001"
    summary_path = run_dir / "summary.md"
    state_path = service_root / "hud_state.json"

    _write_synthetic_summary(summary_path)
    _write_synthetic_state(state_path, run_dir, summary_path)

    snapshot = compute_training_hud(state_path=state_path, summary_path=summary_path)

    summary_fields = parse_summary_key_fields(summary_path)

    hud_markers = {
        "mode": snapshot.mode,
        "kill": snapshot.kill_switch,
        "data_health": snapshot.data_health,
        "stage": snapshot.stage,
        "run_id": snapshot.run_id,
        "elapsed": snapshot.elapsed,
        "next": snapshot.next_iteration,
        "max_drawdown": snapshot.risk.get("max_drawdown"),
        "turnover": snapshot.risk.get("turnover"),
        "reject_count": snapshot.risk.get("reject_count"),
        "gates_triggered": snapshot.risk.get("gates_triggered"),
        "equity": snapshot.equity,
    }

    for key, value in hud_markers.items():
        if value in (None, "", MISSING_FIELD_TEXT):
            errors.append(f"Missing HUD field for {key}")

    reject_lines = "\n".join(summary_fields.reject_reasons_top3)
    if "risk_limit" not in reject_lines:
        errors.append("Expected rejection reasons to include synthetic markers")

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        print("=== VERIFY_UI_HUD_PARSING FAIL ===")
        return 1

    print("PASS: HUD parser surfaced synthetic risk + status markers")
    print("=== VERIFY_UI_HUD_PARSING PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
