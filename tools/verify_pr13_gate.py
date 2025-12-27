from __future__ import annotations

import json
import py_compile
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.git_baseline_probe import probe_baseline
from tools.progress_diagnose import compute_progress_diagnosis
from tools.progress_index import build_progress_index
from tools.progress_plot import compute_polyline

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"


def _using_venv() -> int:
    exe = str(Path(sys.executable).resolve())
    prefix = str(Path(sys.prefix).resolve())
    return 1 if ".venv" in exe or ".venv" in prefix else 0


def _compile_targets(paths: List[Path]) -> List[str]:
    failures = []
    for path in paths:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path}: {exc.msg}")
    return failures


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_equity_curve(path: Path, equities: List[float]) -> None:
    lines = ["ts_utc,equity_usd,cash_usd,drawdown_pct"]
    for idx, value in enumerate(equities):
        lines.append(f"2024-01-01T00:00:{idx:02d}Z,{value:.2f},{value:.2f},0.0")
    path.write_text("\n".join(lines), encoding="utf-8")


def _summary_payload(start_equity: float, end_equity: float) -> Dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_version": "SIM",
        "start_equity": start_equity,
        "end_equity": end_equity,
        "net_change": end_equity - start_equity,
        "max_drawdown": 0.0,
        "turnover": 0.0,
        "rejects_count": 0,
        "gates_triggered": "",
        "stop_reason": "cooldown",
        "timestamps": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T00:10:00Z"},
        "parse_warnings": [],
    }


def _holdings_payload() -> Dict[str, object]:
    return {"positions": {"AAPL": 1}, "cash_usd": 1000.0}


def _synthesize_runs(root: Path) -> List[Path]:
    root.mkdir(parents=True, exist_ok=True)
    run_dirs = []
    for idx in range(4):
        run_dir = root / f"run_{idx}"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_dirs.append(run_dir)
    _write_equity_curve(run_dirs[0] / "equity_curve.csv", [100, 101, 102])
    _write_json(run_dirs[0] / "summary.json", _summary_payload(100, 102))
    _write_json(run_dirs[0] / "holdings.json", _holdings_payload())
    _write_json(
        run_dirs[0] / "run_meta.json",
        {
            "run_id": run_dirs[0].name,
            "stop_reason": "cooldown",
            "steps_completed": 3,
            "trades": 0,
            "rejects": {},
            "gates_triggered": [],
        },
    )
    _write_json(
        run_dirs[0] / "run_complete.json",
        {
            "schema_version": 1,
            "created_utc": "2024-01-01T00:10:00Z",
            "run_id": run_dirs[0].name,
            "status": "complete",
            "artifacts": {
                "equity_curve.csv": str(run_dirs[0] / "equity_curve.csv"),
                "summary.json": str(run_dirs[0] / "summary.json"),
                "holdings.json": str(run_dirs[0] / "holdings.json"),
                "run_meta.json": str(run_dirs[0] / "run_meta.json"),
            },
        },
    )

    _write_json(run_dirs[1] / "summary.json", _summary_payload(100, 99))
    _write_json(run_dirs[1] / "holdings.json", _holdings_payload())

    _write_equity_curve(run_dirs[2] / "equity_curve.csv", [100, 98, 97])
    _write_json(run_dirs[2] / "holdings.json", _holdings_payload())

    _write_equity_curve(run_dirs[3] / "equity_curve.csv", [100, 103, 105])
    _write_json(run_dirs[3] / "summary.json", _summary_payload(100, 105))
    return run_dirs


def _assert_progress_index(runs_root: Path) -> None:
    payload = build_progress_index(runs_root, max_runs=10)
    entries = payload.get("entries", [])
    if not isinstance(entries, list) or len(entries) < 3:
        raise AssertionError("runs_found < 3 in progress_index")
    missing_found = any(entry.get("missing_reason") for entry in entries if isinstance(entry, dict))
    if not missing_found:
        raise AssertionError("missing_reason not populated when artifacts are absent")


def _assert_polyline() -> None:
    series = [100.0, 110.0, 105.0, 120.0]
    polyline = compute_polyline(series, width=200, height=100, padding=10)
    if len(polyline) != len(series):
        raise AssertionError("polyline length mismatch")
    xs = [pt[0] for pt in polyline]
    ys = [pt[1] for pt in polyline]
    if any(x < 0 or x > 200 for x in xs):
        raise AssertionError("polyline x out of bounds")
    if any(y < 0 or y > 100 for y in ys):
        raise AssertionError("polyline y out of bounds")
    if any(xs[i] > xs[i + 1] for i in range(len(xs) - 1)):
        raise AssertionError("polyline x not non-decreasing")
    for i in range(len(series)):
        for j in range(len(series)):
            if series[i] > series[j] and ys[i] >= ys[j]:
                raise AssertionError("polyline y does not invert equity scale")


def _write_state(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, payload)


def _assert_diagnose_priority(tmp_dir: Path) -> None:
    now = datetime.now(timezone.utc)
    state_path = tmp_dir / "state.json"
    progress_index_path = tmp_dir / "progress_index.json"
    _write_json(progress_index_path, {"entries": []})

    kill_switch = tmp_dir / "KILL_SWITCH"
    kill_switch.write_text("1", encoding="utf-8")
    _write_state(
        state_path,
        {
            "last_heartbeat_ts": (now - timedelta(seconds=400)).isoformat(),
            "stop_reason": "max_episodes_per_day",
            "last_error": "data error",
        },
    )
    diag = compute_progress_diagnosis(
        state_path=state_path,
        progress_index_path=progress_index_path,
        kill_switch_paths=[kill_switch],
        now=now,
    )
    if diag.get("primary_reason") != "kill_switch_tripped":
        raise AssertionError("priority order failed for kill_switch")

    _write_state(
        state_path,
        {
            "last_heartbeat_ts": (now - timedelta(seconds=400)).isoformat(),
            "stop_reason": None,
        },
    )
    diag = compute_progress_diagnosis(
        state_path=state_path,
        progress_index_path=progress_index_path,
        kill_switch_paths=[],
        now=now,
    )
    summary = str(diag.get("summary", ""))
    if diag.get("primary_reason") != "service_heartbeat_stale":
        raise AssertionError("priority order failed for heartbeat")
    if "heartbeat_age_s" not in summary:
        raise AssertionError("heartbeat detail missing in diagnosis summary")

    _write_state(
        state_path,
        {
            "last_heartbeat_ts": now.isoformat(),
            "next_iteration_eta": (now + timedelta(seconds=120)).isoformat(),
        },
    )
    diag = compute_progress_diagnosis(
        state_path=state_path,
        progress_index_path=progress_index_path,
        kill_switch_paths=[],
        now=now,
    )
    summary = str(diag.get("summary", ""))
    if diag.get("primary_reason") != "cooldown_backoff_waiting":
        raise AssertionError("priority order failed for cooldown")
    if "next_run_in" not in summary:
        raise AssertionError("cooldown detail missing in diagnosis summary")


def main() -> int:
    status = "PASS"
    reasons: List[str] = []
    degraded_reasons: List[str] = []

    print("PR13_GATE_START")
    baseline_info = probe_baseline()
    baseline = baseline_info.get("baseline") or "unavailable"
    baseline_status = baseline_info.get("status") or "UNAVAILABLE"
    baseline_details = baseline_info.get("details") or "unknown"
    if baseline_status != "AVAILABLE":
        degraded_reasons.append(f"baseline_unavailable_{baseline_details}")
    summary_top = "|".join(
        [
            "PR13_GATE_SUMMARY",
            "status=RUNNING",
            "degraded=0",
            "degraded_reasons=none",
            f"using_venv={_using_venv()}",
            f"baseline={baseline}",
            f"baseline_status={baseline_status}",
            f"baseline_details={baseline_details}",
            "reasons=running",
        ]
    )
    print(summary_top)

    try:
        compile_failures = _compile_targets(
            [
                ROOT / "tools" / "ui_app.py",
                ROOT / "tools" / "progress_index.py",
                ROOT / "tools" / "progress_diagnose.py",
                ROOT / "tools" / "progress_plot.py",
                Path(__file__),
            ]
        )
        if compile_failures:
            raise AssertionError("py_compile failures: " + "; ".join(compile_failures))

        RUNS_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=RUNS_ROOT) as tmp_runs:
            runs_root = Path(tmp_runs)
            _synthesize_runs(runs_root)
            _assert_progress_index(runs_root)

        _assert_polyline()

        with tempfile.TemporaryDirectory() as tmp_state:
            _assert_diagnose_priority(Path(tmp_state))
    except Exception as exc:
        status = "FAIL"
        reasons.append(str(exc))

    degraded = 1 if degraded_reasons else 0
    allowed_degraded = {"ui_display_unavailable"}
    if any(
        reason not in allowed_degraded and not reason.startswith("baseline_unavailable_")
        for reason in degraded_reasons
    ):
        status = "FAIL"
        reasons.append("invalid_degraded_reason")

    summary = "|".join(
        [
            "PR13_GATE_SUMMARY",
            f"status={status}",
            f"degraded={degraded}",
            f"degraded_reasons={','.join(degraded_reasons) if degraded_reasons else 'none'}",
            f"using_venv={_using_venv()}",
            f"baseline={baseline}",
            f"baseline_status={baseline_status}",
            f"baseline_details={baseline_details}",
            f"reasons={';'.join(reasons) if reasons else 'none'}",
        ]
    )
    print(summary)
    print("PR13_GATE_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
