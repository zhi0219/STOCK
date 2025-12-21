import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_JUDGE = ROOT / "tools" / "progress_judge.py"


def _write_quotes(path: Path) -> None:
    rows = [
        "ts_utc,symbol,price",
        "2024-01-01T00:00:00+00:00,AAPL,100",
        "2024-01-01T00:01:00+00:00,AAPL,101",
        "2024-01-01T00:02:00+00:00,AAPL,102",
        "2024-01-01T00:03:00+00:00,AAPL,103",
        "2024-01-01T00:04:00+00:00,AAPL,104",
    ]
    path.write_text("\n".join(rows), encoding="utf-8")


def _write_judge(path: Path) -> None:
    body = {
        "windows": [
            {"name": "short", "start_row": 1, "count": 3},
            {"name": "tail", "start_row": 3, "count": 3},
        ]
    }
    path.write_text(json.dumps(body), encoding="utf-8")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _assert_marker(text: str, marker: str) -> None:
    if marker not in text:
        raise AssertionError(f"Missing marker: {marker}")


def run() -> int:
    if not PROGRESS_JUDGE.exists():
        print("VERIFY_PROGRESS_TRUTH_SUMMARY|status=SKIP|reason=missing progress_judge.py")
        return 0

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        quotes_path = base / "quotes.csv"
        judge_cfg = base / "judge.yaml"
        reports_root = base / "Reports"
        state_path = base / "judge_state.json"
        _write_quotes(quotes_path)
        _write_judge(judge_cfg)

        cmd = [
            sys.executable,
            str(PROGRESS_JUDGE),
            "--config",
            str(judge_cfg),
            "--quotes",
            str(quotes_path),
            "--reports-root",
            str(reports_root),
            "--state-path",
            str(state_path),
            "--runs-root",
            str(base / "train_runs"),
        ]
        result = _run(cmd)
        stdout = result.stdout or ""
        _assert_marker(stdout, "JUDGE_START")
        _assert_marker(stdout, "JUDGE_END")
        _assert_marker(stdout, "JUDGE_SUMMARY")
        _assert_marker(stdout, "BASELINE_COMPARISON")
        _assert_marker(stdout, "PROMOTION_RECOMMENDATION")
        if state_path.exists():
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if payload.get("verify_no_lookahead") != 1:
                raise AssertionError("verify_no_lookahead flag missing in state")
        else:
            raise AssertionError("judge_state.json missing")

        degraded = "status=DEGRADED" in stdout
        if result.returncode != 0 and not degraded:
            print(stdout)
            print(result.stderr)
            print("VERIFY_PROGRESS_TRUTH_SUMMARY|status=FAIL|reason=non-zero exit")
            return 1

        # Invalid config should fail closed to DEGRADED
        missing_cfg = base / "missing.yaml"
        bad_cmd = [
            sys.executable,
            str(PROGRESS_JUDGE),
            "--config",
            str(missing_cfg),
            "--quotes",
            str(quotes_path),
            "--reports-root",
            str(reports_root),
            "--state-path",
            str(state_path),
            "--runs-root",
            str(base / "train_runs"),
        ]
        bad = _run(bad_cmd)
        if "status=DEGRADED" not in (bad.stdout or ""):
            print(bad.stdout)
            print("Expected DEGRADED when config missing")
            print("VERIFY_PROGRESS_TRUTH_SUMMARY|status=FAIL|reason=missing-config")
            return 1

        # Kill switch veto
        kill_switch = ROOT / "Data" / "KILL_SWITCH"
        kill_switch.parent.mkdir(parents=True, exist_ok=True)
        try:
            kill_switch.write_text("stop", encoding="utf-8")
            ks_result = _run(cmd)
            if "KILL_SWITCH" not in ks_result.stdout:
                raise AssertionError("Kill switch not reported")
            if "status=DEGRADED" not in ks_result.stdout:
                raise AssertionError("Kill switch did not degrade run")
        finally:
            try:
                kill_switch.unlink()
            except Exception:
                pass

        # Anti-lookahead enforcement asserted via marker and exit code
        status_line = next((line for line in stdout.splitlines() if line.startswith("JUDGE_SUMMARY")), "")
        if "verify_no_lookahead=1" not in status_line:
            raise AssertionError("verify_no_lookahead marker missing")

        print("VERIFY_PROGRESS_TRUTH_SUMMARY|status=PASS|message=markers present and fail-closed")
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
