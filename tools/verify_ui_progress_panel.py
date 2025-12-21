from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

try:
    import tkinter as tk
except Exception as exc:  # pragma: no cover - headless detection
    print(f"PROGRESS_UI_SUMMARY|status=SKIP|reason=tkinter_import_failed|detail={exc}")
    raise SystemExit(0)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.progress_index import build_progress_index, write_progress_index


def _display_available() -> bool:
    try:
        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        return True
    except Exception as exc:  # pragma: no cover - headless
        print(f"PROGRESS_UI_SUMMARY|status=SKIP|reason=headless|detail={exc}")
        return False


def _seed_progress_index(base: Path) -> Path:
    runs_root = base / "train_runs"
    run_dir = runs_root / "2024-01-02" / "run_002"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.md").write_text(
        "\n".join(
            [
                "# Summary",
                "Stop reason: completed",
                "Net value change: +1.0%",
                "Max drawdown: -0.5%",
                "Trades executed: 2",
                "Turnover: 4",
                "Reject count: 0",
                "Gates triggered: none",
                "## Rejection reasons",
                "- none",
            ]
        ),
        encoding="utf-8",
    )
    (run_dir / "equity_curve.csv").write_text(
        "ts_utc,equity_usd,cash_usd,drawdown_pct,step,policy_version,mode\n"
        "2024-01-02T00:00:00+00:00,10000,10000,0,1,v1,NORMAL\n"
        "2024-01-02T00:02:00+00:00,10100,10020,-0.01,2,v1,NORMAL\n",
        encoding="utf-8",
    )
    (run_dir / "orders_sim.jsonl").write_text(json.dumps({"symbol": "SIM", "pnl": 1}), encoding="utf-8")
    payload = build_progress_index(runs_root)
    output_path = runs_root / "progress_index.json"
    write_progress_index(payload, output_path)
    return output_path


def run() -> int:
    if not _display_available():
        return 0

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        index_path = _seed_progress_index(base)

        from tools.ui_app import App  # imported lazily to avoid headless init

        app = App()
        try:
            app.progress_index_path = index_path
            app._load_progress_index()
            app.update_idletasks()
        finally:
            app.destroy()

    print("PROGRESS_UI_SUMMARY|status=PASS|reason=rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
