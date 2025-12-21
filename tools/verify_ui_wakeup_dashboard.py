"""Sanity checks for wake-up dashboard helpers and train_daemon outputs."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.wakeup_dashboard import (  # noqa: E402
    MISSING_FIELD_TEXT,
    find_latest_run_dir,
    find_latest_summary_md,
    parse_summary_key_fields,
)
RUNS_ROOT = ROOT / "Logs" / "train_runs"


def _write_fake_run() -> Path:
    day_dir = RUNS_ROOT / "verify_ui"
    run_dir = day_dir / f"run_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = run_dir / "summary.md"
    summary.write_text(
        "\n".join(
            [
                "# Train Daemon Summary",
                "",
                "Run: verify",
                "Policy: test",
                "Stop reason: max_runtime",
                "Net value change: +1.23 USD",
                "Max drawdown: 2.50%",
                "Trades executed: 7",
                "",
                "## Rejection reasons (top 5)",
                "- rule_a: 2",
                "- rule_b: 1",
            ]
        ),
        encoding="utf-8",
    )
    return summary


def _ensure_fake_quotes() -> Path:
    quotes_path = ROOT / "Data" / "quotes.csv"
    quotes_path.parent.mkdir(parents=True, exist_ok=True)
    if not quotes_path.exists():
        quotes_path.write_text(
            "symbol,ts_utc,price\nAAPL,2024-01-01T00:00:00+00:00,100\n",
            encoding="utf-8",
        )
    return quotes_path


def _assert_latest_summary(expected_summary: Path) -> None:
    run_dir, summary_path = find_latest_summary_md(RUNS_ROOT)
    assert run_dir is not None, "expected a run dir to be found"
    assert summary_path == expected_summary, f"unexpected summary path {summary_path}"
    parsed = parse_summary_key_fields(summary_path)
    assert parsed.summary_path == summary_path
    assert parsed.net_change != MISSING_FIELD_TEXT
    assert parsed.reject_reasons_top3, "expected rejection reasons to be parsed"


def _assert_train_daemon_short_run() -> None:
    quotes_path = _ensure_fake_quotes()
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "train_daemon.py"),
        "--max-runtime-seconds",
        "3",
        "--input",
        str(quotes_path),
        "--retain-days",
        "1",
        "--retain-latest-n",
        "3",
        "--max-total-train-runs-mb",
        "50",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, f"train_daemon exited with {proc.returncode}: {proc.stderr}"
    run_dir, summary_path = find_latest_summary_md(RUNS_ROOT)
    assert summary_path is not None and summary_path.exists(), "train_daemon did not write summary"
    parsed = parse_summary_key_fields(summary_path)
    assert parsed.summary_path == summary_path
    assert parsed.net_change, "net_change should be populated"


def main() -> None:
    fake_summary = _write_fake_run()
    _assert_latest_summary(fake_summary)
    _assert_train_daemon_short_run()
    latest_run = find_latest_run_dir(RUNS_ROOT)
    assert latest_run is not None and latest_run.exists(), "latest run dir should exist"
    print("verify_ui_wakeup_dashboard: ok")


if __name__ == "__main__":
    main()
