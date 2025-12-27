from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import tkinter as tk

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
UI_APP = ROOT / "tools" / "ui_app.py"
SUMMARY_TAG = "UI_SMOKE_SUMMARY"
LATEST_PATH = LOGS_DIR / "ui_smoke_latest.json"
RUN_SECONDS = 3.0


def _display_available() -> tuple[bool, str]:
    try:
        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        return True, ""
    except Exception as exc:  # pragma: no cover - headless
        return False, str(exc)


def _summary_line(status: str, degraded: bool, reason: str, detail: str) -> str:
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"degraded={1 if degraded else 0}",
            f"reason={reason}",
            f"detail={detail}",
        ]
    )


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _record_latest(status: str, degraded: bool, reason: str, detail: str, runtime: float) -> None:
    payload = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "degraded": int(degraded),
        "reason": reason,
        "detail": detail,
        "runtime_seconds": runtime,
        "python": sys.executable,
    }
    _atomic_write(LATEST_PATH, payload)


def _utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def main() -> int:
    print("UI_SMOKE_START")
    display_ok, detail = _display_available()
    if not display_ok:
        summary = _summary_line(
            status="SKIP",
            degraded=True,
            reason="ui_display_unavailable",
            detail=detail or "unknown",
        )
        _record_latest("SKIP", True, "ui_display_unavailable", detail or "unknown", 0.0)
        print(summary)
        print("UI_SMOKE_END")
        print(summary)
        return 0

    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            [sys.executable, str(UI_APP)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
        )
    except Exception as exc:
        summary = _summary_line("FAIL", False, "spawn_failed", str(exc))
        _record_latest("FAIL", False, "spawn_failed", str(exc), 0.0)
        print(summary)
        print("UI_SMOKE_END")
        print(summary)
        return 1

    early_exit = False
    while time.monotonic() - start < RUN_SECONDS:
        if proc.poll() is not None:
            early_exit = True
            break
        time.sleep(0.1)

    runtime = time.monotonic() - start
    if not early_exit:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    if early_exit:
        stdout = (proc.stdout.read() if proc.stdout else "").strip()
        stderr = (proc.stderr.read() if proc.stderr else "").strip()
        detail_text = f"exit_code={proc.returncode}"
        if stderr:
            detail_text += f";stderr={stderr}"
        elif stdout:
            detail_text += f";stdout={stdout}"
        summary = _summary_line("FAIL", False, "ui_app_exited_early", detail_text)
        _record_latest("FAIL", False, "ui_app_exited_early", detail_text, runtime)
        print(summary)
        print("UI_SMOKE_END")
        print(summary)
        return 1

    summary = _summary_line("PASS", False, "ran", f"runtime={runtime:.2f}s")
    _record_latest("PASS", False, "ran", f"runtime={runtime:.2f}s", runtime)
    print(summary)
    print("UI_SMOKE_END")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
