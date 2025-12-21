from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import tkinter as tk
except Exception as exc:  # pragma: no cover - environment guard
    print(f"VERIFY_UI_PROGRESS_PANEL_SUMMARY|status=SKIP|reason={exc}")
    print(f"VERIFY_UI_PROGRESS_PANEL_SUMMARY|status=SKIP|reason={exc}")
    sys.exit(0)

from tools import ui_app
from tools.ui_app import ascii_sparkline, render_polyline


def main() -> int:
    print("VERIFY_UI_PROGRESS_PANEL_SUMMARY|status=START")
    ui_app.App._start_auto_refresh = lambda self: None  # type: ignore[assignment]
    try:
        app = ui_app.App()
    except tk.TclError as exc:  # pragma: no cover - environment guard
        print(f"VERIFY_UI_PROGRESS_PANEL_SUMMARY|status=SKIP|reason={exc}")
        print(f"VERIFY_UI_PROGRESS_PANEL_SUMMARY|status=SKIP|reason={exc}")
        return 0
    app.withdraw()

    fake_run = {
        "run_dir": "Logs/train_runs/fake/run1",
        "summary_path": "Logs/train_runs/fake/run1/summary.md",
        "equity_curve": "Logs/train_runs/fake/run1/equity_curve.csv",
        "final_equity": 1234.5,
        "max_drawdown": 2.5,
        "turnover": 0.2,
        "reject_count": 1,
        "gate_triggers": "risk_test",
        "timestamp": "2024-01-02T00:00:00",
        "holdings_path": None,
        "cash_usd": 1000.0,
    }
    fake_index = {
        "status": "PASS",
        "runs": [fake_run],
        "latest_run": fake_run,
        "best_equity_run": fake_run,
        "best_drawdown_run": fake_run,
    }
    app._render_progress_index(fake_index)
    render_polyline(app.progress_curve_canvas, [1, 2, 3, 2, 4], color="#4ade80")
    render_polyline(app.progress_runs_canvas, [10, 11, 9, 12], color="#f97316")
    ascii_sparkline([1, 2, 3])
    app.destroy()
    print("VERIFY_UI_PROGRESS_PANEL_SUMMARY|status=PASS|steps=initialized")
    print("VERIFY_UI_PROGRESS_PANEL_SUMMARY|status=PASS|steps=initialized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
