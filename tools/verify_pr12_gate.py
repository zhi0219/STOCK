from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.git_baseline_probe import probe_baseline

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_SCRIPT = ROOT / "tools" / "progress_index.py"
SUMMARY_TAG = "PR12_GATE_SUMMARY"
REPO_HYGIENE = ROOT / "tools" / "verify_repo_hygiene.py"
UI_PROGRESS_VERIFY = ROOT / "tools" / "verify_ui_progress_panel.py"
LOGS_DIR = ROOT / "Logs"
BASELINE_LOCAL = ROOT / ".git"

PY_COMPILE_TARGETS = [
    ROOT / "tools" / "progress_index.py",
    ROOT / "tools" / "train_daemon.py",
    ROOT / "tools" / "ui_app.py",
    ROOT / "tools" / "verify_pr12_gate.py",
]


def _seed_run(base: Path) -> Path:
    runs_root = base / "train_runs"
    run_dir = runs_root / "2024-03-03" / "run_pr12_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "policy_version": "v1",
                "start_equity": 10000.0,
                "end_equity": 10150.0,
                "net_change": 150.0,
                "max_drawdown": 0.75,
                "turnover": 5,
                "rejects_count": 1,
                "gates_triggered": [],
                "stop_reason": "completed",
                "timestamps": {"start": "2024-03-03T00:00:00+00:00", "end": "2024-03-03T00:05:00+00:00"},
                "parse_warnings": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "holdings.json").write_text(
        json.dumps(
            {
                "timestamp": "2024-03-03T00:05:00+00:00",
                "cash_usd": 10050.0,
                "positions": {"SIM": 1.0},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "equity_curve.csv").write_text(
        "ts_utc,equity_usd,cash_usd,drawdown_pct,step,policy_version,mode\n"
        "2024-03-03T00:00:00+00:00,10000,10000,0,1,v1,NORMAL\n"
        "2024-03-03T00:05:00+00:00,10150,10050,0.75,2,v1,NORMAL\n",
        encoding="utf-8",
    )
    return runs_root


def _summary_line(status: str, reason: str, baseline: str, baseline_status: str, baseline_details: str) -> str:
    detail = reason or "ok"
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"reason={detail}",
            f"baseline={baseline}",
            f"baseline_status={baseline_status}",
            f"baseline_details={baseline_details}",
        ]
    )


def _run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _check_repo_root() -> tuple[bool, str]:
    if Path.cwd().resolve() != ROOT:
        return False, f"wrong_cwd:{Path.cwd().resolve()}"
    if not BASELINE_LOCAL.exists():
        return False, "missing_git_dir"
    return True, "ok"


def _using_venv() -> bool:
    if sys.prefix != sys.base_prefix:
        return True
    return bool(os.environ.get("VIRTUAL_ENV"))


def _check_logs_writable() -> tuple[bool, str]:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        probe = LOGS_DIR / ".pr12_gate_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        return False, f"logs_not_writable:{exc}"
    return True, "ok"


def _check_repo_hygiene() -> tuple[bool, str]:
    if not REPO_HYGIENE.exists():
        return False, "repo_hygiene_missing"
    result = _run_cmd([sys.executable, str(REPO_HYGIENE)])
    if result.returncode != 0:
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return False, f"repo_hygiene_failed:{output.strip()}"
    return True, "ok"


def _check_py_compile() -> tuple[bool, str]:
    args = [str(path) for path in PY_COMPILE_TARGETS]
    result = _run_cmd([sys.executable, "-m", "py_compile", *args])
    if result.returncode != 0:
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return False, f"py_compile_failed:{output.strip()}"
    return True, "ok"


def _verify_ui_progress() -> tuple[bool, str]:
    if not UI_PROGRESS_VERIFY.exists():
        return True, "ui_verify_missing_skipped"
    result = _run_cmd([sys.executable, str(UI_PROGRESS_VERIFY)])
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    if "status=SKIP" in output:
        return True, "ui_verify_skipped"
    if result.returncode != 0:
        return False, f"ui_verify_failed:{output.strip()}"
    return True, "ok"


def run() -> int:
    status = "PASS"
    reasons: list[str] = []
    degraded = False
    degraded_reasons: list[str] = []
    progress_result: subprocess.CompletedProcess[str] | None = None

    ok, reason = _check_repo_root()
    if not ok:
        status = "FAIL"
        reasons.append(reason)
    else:
        ok, reason = _check_logs_writable()
        if not ok:
            status = "FAIL"
            reasons.append(reason)
        else:
            ok, reason = _check_repo_hygiene()
            if not ok:
                status = "FAIL"
                reasons.append(reason)
            else:
                ok, reason = _check_py_compile()
                if not ok:
                    status = "FAIL"
                    reasons.append(reason)

    baseline_info = probe_baseline()
    baseline = baseline_info.get("baseline") or "unavailable"
    baseline_status = baseline_info.get("status") or "UNAVAILABLE"
    baseline_details = baseline_info.get("details") or "unknown"
    if baseline_status != "AVAILABLE":
        degraded = True
        degraded_reasons.append(f"baseline_unavailable_{baseline_details}")

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        runs_root = _seed_run(base)
        output_path = runs_root / "progress_index.json"

        if status == "PASS":
            cmd = [sys.executable, str(PROGRESS_SCRIPT), "--runs-root", str(runs_root), "--output", str(output_path)]
            progress_result = _run_cmd(cmd)
            if progress_result.returncode != 0:
                status = "FAIL"
                reasons.append(f"progress_index_failed_rc_{progress_result.returncode}")
            elif not output_path.exists():
                status = "FAIL"
                reasons.append("progress_index_missing")
            else:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
                entries = payload.get("entries", []) if isinstance(payload, dict) else []
                if not entries:
                    status = "FAIL"
                    reasons.append("entries_missing")
                else:
                    entry = entries[0]
                    required = [
                        "run_id",
                        "run_dir",
                        "mtime",
                        "summary",
                        "has_equity_curve",
                        "has_summary_json",
                        "has_holdings_json",
                        "parse_error",
                        "still_writing",
                        "missing_reason",
                    ]
                    missing = [key for key in required if key not in entry]
                    if missing:
                        status = "FAIL"
                        reasons.append(f"missing_fields:{','.join(missing)}")
                    elif entry.get("missing_reason"):
                        status = "FAIL"
                        reasons.append(f"unexpected_missing_reason:{entry.get('missing_reason')}")
                    elif entry.get("parse_error"):
                        status = "FAIL"
                        reasons.append("parse_error_set")

        ui_ok, ui_reason = _verify_ui_progress()
        if not ui_ok:
            status = "FAIL"
            reasons.append(ui_reason)
        elif ui_reason == "ui_verify_skipped":
            degraded = True
            degraded_reasons.append("ui_display_unavailable")

    reasons_text = ";".join(reasons) if reasons else "ok"
    summary = "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"degraded={1 if degraded else 0}",
            f"reasons={reasons_text}",
            f"degraded_reasons={';'.join(degraded_reasons) if degraded_reasons else 'none'}",
            f"using_venv={1 if _using_venv() else 0}",
            f"baseline={baseline}",
            f"baseline_status={baseline_status}",
            f"baseline_details={baseline_details}",
        ]
    )
    print("PR12_GATE_START")
    print(summary)
    if progress_result:
        if progress_result.stdout:
            print(progress_result.stdout.strip())
        if progress_result.stderr:
            print(progress_result.stderr.strip())
    print("PR12_GATE_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(run())
