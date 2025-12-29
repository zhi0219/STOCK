from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable
from datetime import datetime, timedelta, timezone

from tools.promotion_gate_v2 import GateConfig, evaluate_promotion_gate

ARTIFACTS_DIR = Path("artifacts")
RUNS_ROOT = Path("Logs") / "train_runs" / "_pr35_gate"
ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\")


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


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


def _latest_run_dir(runs_root: Path) -> Path | None:
    if not runs_root.exists():
        return None
    run_dirs = []
    for day_dir in runs_root.iterdir():
        if not day_dir.is_dir():
            continue
        for run_dir in day_dir.iterdir():
            if run_dir.is_dir():
                run_dirs.append(run_dir)
    if not run_dirs:
        return None
    return max(run_dirs, key=lambda p: p.stat().st_mtime)


def _run_command(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = "\n".join(block for block in [result.stdout, result.stderr] if block)
    return result.returncode, combined.strip()


def _write_quotes_csv(path: Path, rows: int = 120) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = datetime.now(timezone.utc) - timedelta(days=1)
    price = 100.0
    lines = ["ts_utc,symbol,price"]
    for idx in range(rows):
        price += 0.1 if idx % 2 == 0 else -0.05
        ts = start + timedelta(minutes=5 * idx)
        lines.append(f"{ts.isoformat()},SIM,{price:.2f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_artifact(source: Path, target: Path) -> None:
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    quotes_path = RUNS_ROOT / "pr35_quotes.csv"
    _write_quotes_csv(quotes_path, rows=140)
    cmd = [
        sys.executable,
        "-m",
        "tools.train_daemon",
        "--runs-root",
        str(RUNS_ROOT),
        "--input",
        str(quotes_path),
        "--max-steps",
        "80",
        "--max-trades",
        "6",
        "--max-runtime-seconds",
        "2",
        "--seed",
        "135",
        "--max-log-mb",
        "4",
    ]
    rc, output = _run_command(cmd)
    if rc != 0:
        errors.append(f"train_daemon_failed:{output}")

    run_dir = _latest_run_dir(RUNS_ROOT)
    if not run_dir:
        errors.append("run_dir_missing")
        run_dir = RUNS_ROOT / "_missing"

    run_complete = run_dir / "run_complete.json"
    if not run_complete.exists():
        errors.append("run_complete_missing")

    report_path = run_dir / "stress_report.json"
    scenarios_path = run_dir / "stress_scenarios.jsonl"
    if not report_path.exists():
        errors.append("stress_report_missing")
    if not scenarios_path.exists():
        errors.append("stress_scenarios_missing")

    report_payload = _safe_read_json(report_path)
    if not report_payload:
        errors.append("stress_report_invalid")
    else:
        required_fields = ["schema_version", "created_utc", "run_id", "status", "scenarios", "evidence"]
        missing = [field for field in required_fields if field not in report_payload]
        if missing:
            errors.append(f"stress_report_missing_fields:{','.join(missing)}")
        errors.extend(_assert_repo_relative(report_payload))

    scenario_rows = _safe_read_jsonl(scenarios_path)
    if not scenario_rows:
        errors.append("stress_scenarios_empty")
    else:
        for row in scenario_rows:
            if not isinstance(row, dict):
                errors.append("stress_scenarios_invalid")
                break
            errors.extend(_assert_repo_relative(row))
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            required_metrics = ["return_pct", "max_drawdown_pct", "turnover"]
            missing_metrics = [key for key in required_metrics if key not in metrics]
            if missing_metrics:
                errors.append(f"stress_metrics_missing:{','.join(missing_metrics)}")
                break
            if "scenario" not in row or "multipliers" not in row or "pass" not in row:
                errors.append("stress_scenario_missing_fields")
                break

    candidate = {
        "candidate_id": "candidate_ok",
        "score": 99.0,
        "max_drawdown_pct": 1.0,
        "turnover": 0,
        "reject_rate": 0.0,
    }
    baselines = [{"candidate_id": "baseline", "score": -1.0}]
    decision = evaluate_promotion_gate(candidate, baselines, "pr35_missing", GateConfig(), stress_report=None)
    if decision.get("decision") != "REJECT":
        errors.append("stress_missing_not_rejected")

    _copy_artifact(report_path, ARTIFACTS_DIR / "Logs" / "train_runs" / "_pr35_gate" / "stress_report.json")
    _copy_artifact(
        scenarios_path,
        ARTIFACTS_DIR / "Logs" / "train_runs" / "_pr35_gate" / "stress_scenarios.jsonl",
    )

    if os.environ.get("PR35_FORCE_FAIL") == "1":
        errors.append("PR35_FORCE_FAIL")

    if errors:
        print("verify_pr35_gate FAIL")
        for reason in errors:
            print(f" - {reason}")
        return 1

    print("verify_pr35_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
