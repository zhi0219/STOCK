from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ui_parsers import (
    load_engine_status,
    load_policy_history_latest,
    load_progress_judge_latest,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_py_compile(targets: List[Path]) -> tuple[bool, str]:
    args = [str(path) for path in targets]
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return False, output.strip()
    return True, "ok"


def _run_repo_hygiene() -> tuple[bool, str]:
    script = TOOLS_DIR / "verify_repo_hygiene.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    return result.returncode == 0, output.strip()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _exercise_ui_parsers(base: Path) -> tuple[bool, str]:
    latest_dir = base / "_latest"
    created = _now()
    tournament_path = latest_dir / "tournament_latest.json"
    decision_path = latest_dir / "promotion_decision_latest.json"
    judge_path = latest_dir / "progress_judge_latest.json"
    history_path = latest_dir / "policy_history_latest.json"

    _write_json(
        tournament_path,
        {
            "schema_version": 1,
            "created_utc": created,
            "run_id": "run_synth",
            "policy_version": "baseline",
        },
    )
    _write_json(
        decision_path,
        {
            "schema_version": 1,
            "created_utc": created,
            "run_id": "run_synth",
            "policy_version": "baseline",
        },
    )
    _write_json(
        judge_path,
        {
            "schema_version": "1.0",
            "created_utc": created,
            "run_id": "run_synth",
            "policy_version": "baseline",
            "recommendation": "INSUFFICIENT_DATA",
            "scores": {"vs_do_nothing": None, "vs_buy_hold": None},
            "drivers": [],
            "not_improving_reasons": [],
            "suggested_next_actions": [],
            "trend": {"direction": "unknown", "window": 0, "values": []},
            "risk_metrics": {},
        },
    )
    _write_json(
        history_path,
        {
            "schema_version": 1,
            "created_utc": created,
            "run_id": "run_synth",
            "policy_version": "baseline",
        },
    )

    try:
        _ = load_progress_judge_latest(judge_path)
        _ = load_policy_history_latest(history_path)
        _ = load_engine_status(tournament_path, decision_path, judge_path)
    except Exception as exc:  # pragma: no cover - should not fail
        return False, f"ui_parsers_failed:{exc}"
    return True, "ok"


def _run_throughput_diag(state_path: Path, index_path: Path, latest_dir: Path) -> tuple[bool, str, str]:
    script = TOOLS_DIR / "progress_throughput_diagnose.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--state-path",
            str(state_path),
            "--progress-index-path",
            str(index_path),
            "--latest-dir",
            str(latest_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    summary_line = ""
    for line in output.splitlines():
        if line.startswith("THROUGHPUT_DIAG_SUMMARY"):
            summary_line = line
            break
    if "THROUGHPUT_DIAG_START" not in output or "THROUGHPUT_DIAG_END" not in output:
        return False, "markers_missing", summary_line
    if not summary_line:
        return False, "summary_missing", summary_line
    return True, output.strip(), summary_line


def _synthetic_throughput_checks() -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        logs_root = base / "Logs"
        runs_root = logs_root / "train_runs"
        latest_dir = runs_root / "_latest"
        state_path = logs_root / "train_service" / "state.json"
        progress_index_path = runs_root / "progress_index.json"

        run_dir = runs_root / "2024-01-01" / "run_ok"
        run_dir.mkdir(parents=True, exist_ok=True)

        _write_json(
            state_path,
            {
                "service_start_ts": (now - timedelta(hours=1)).isoformat(),
                "last_episode_end_ts": (now - timedelta(minutes=3)).isoformat(),
                "last_episode_start_ts": (now - timedelta(minutes=5)).isoformat(),
                "episodes_completed": 3,
                "last_error": None,
                "last_run_dir": str(run_dir),
                "last_summary_path": str(run_dir / "summary.md"),
                "service_pid": 1234,
                "last_heartbeat_ts": now.isoformat(),
                "stop_reason": None,
                "cadence_preset": "micro",
                "target_runs_per_hour": 24,
                "computed_runs_per_hour": 3,
                "last_run_duration_s": 90,
                "next_run_eta_s": 60,
                "config": {
                    "episode_seconds": 120,
                    "cooldown_seconds_between_episodes": 5,
                    "max_episodes_per_hour": 24,
                    "max_episodes_per_day": 200,
                    "max_steps": 1500,
                    "max_trades": 200,
                    "max_events_per_hour": 400,
                    "max_disk_mb": 2500,
                    "max_runtime_per_day": 21600,
                    "max_total_train_runs_mb": 5000,
                    "retain_days": 7,
                    "retain_latest_n": 50,
                    "runs_root": str(runs_root),
                },
            },
        )

        _write_json(
            progress_index_path,
            {
                "generated_ts": now.isoformat(),
                "runs_root": str(runs_root),
                "entries": [
                    {
                        "run_id": "run_ok",
                        "run_dir": str(run_dir),
                        "mtime": now.isoformat(),
                    }
                ],
            },
        )

        latest_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "progress_judge_latest.json",
            "promotion_decision_latest.json",
            "tournament_latest.json",
            "candidates_latest.json",
            "policy_history_latest.json",
        ]:
            _write_json(latest_dir / name, {"schema_version": 1, "created_utc": now.isoformat(), "run_id": "run_ok"})

        ok_run, _, ok_summary = _run_throughput_diag(state_path, progress_index_path, latest_dir)
        if not ok_run:
            return False, f"throughput_diag_failed:{ok_summary}"
        if "status=OK" not in ok_summary or "primary_reason=ok" not in ok_summary:
            return False, f"throughput_diag_unexpected:{ok_summary}"

        warn_state = logs_root / "train_service" / "state_warn.json"
        _write_json(
            warn_state,
            {
                "service_start_ts": (now - timedelta(hours=1)).isoformat(),
                "last_heartbeat_ts": (now - timedelta(minutes=10)).isoformat(),
                "stop_reason": None,
            },
        )
        warn_run, _, warn_summary = _run_throughput_diag(warn_state, progress_index_path, latest_dir)
        if not warn_run:
            return False, f"throughput_diag_warn_failed:{warn_summary}"
        if "status=FAIL" not in warn_summary or "primary_reason=service_heartbeat_stale" not in warn_summary:
            return False, f"throughput_diag_warn_unexpected:{warn_summary}"

    return True, "ok"


def main() -> int:
    failures: List[str] = []
    degraded = 0

    print("PR20_GATE_START")

    compile_targets = [
        TOOLS_DIR / "train_service.py",
        TOOLS_DIR / "train_daemon.py",
        TOOLS_DIR / "ui_app.py",
        TOOLS_DIR / "progress_throughput_diagnose.py",
        TOOLS_DIR / "verify_pr20_gate.py",
        TOOLS_DIR / "verify_consistency.py",
    ]
    compile_ok, compile_msg = _run_py_compile(compile_targets)
    if not compile_ok:
        failures.append(f"py_compile_failed:{compile_msg}")

    hygiene_ok, hygiene_msg = _run_repo_hygiene()
    if not hygiene_ok:
        failures.append(f"repo_hygiene_failed:{hygiene_msg}")

    with tempfile.TemporaryDirectory() as tmpdir:
        ok, msg = _exercise_ui_parsers(Path(tmpdir))
        if not ok:
            failures.append(msg)

    diag_ok, diag_msg = _synthetic_throughput_checks()
    if not diag_ok:
        failures.append(f"synthetic_throughput_failed:{diag_msg}")

    status = "PASS" if not failures else "FAIL"
    summary = "|".join(
        [
            "PR20_GATE_SUMMARY",
            f"status={status}",
            f"degraded={degraded}",
            f"failed={len(failures)}",
            f"details={' ; '.join(failures) if failures else 'ok'}",
        ]
    )
    print(summary)
    print("PR20_GATE_END")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
