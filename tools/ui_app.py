from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox
except Exception:
    print("tkinter is required to run this UI. Please ensure Tk is installed.")
    sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "Logs" / "supervisor_state.json"
SUPERVISOR_SCRIPT = ROOT / "tools" / "supervisor.py"


def read_text_tail(path: Path, lines: int = 20) -> str:
    if not path.exists():
        return "(no events file)"
    try:
        with path.open("r", encoding="utf-8") as fh:
            content = fh.readlines()
        return "".join(content[-lines:]) if content else "(empty)"
    except Exception as exc:  # pragma: no cover - UI feedback
        return f"error reading {path}: {exc}"


def latest_events_file() -> Path | None:
    candidates = sorted((ROOT / "Logs").glob("events_*.jsonl"))
    return candidates[-1] if candidates else None


def run_supervisor_command(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SUPERVISOR_SCRIPT), command],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def load_state_text() -> str:
    if not STATE_PATH.exists():
        return "state file not found"
    try:
        return STATE_PATH.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - UI feedback
        return f"error reading state: {exc}"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("STOCK Supervisor")
        self.geometry("800x600")
        self._lock = threading.Lock()
        self._build_ui()
        self._start_auto_refresh()

    def _build_ui(self) -> None:
        top_frame = tk.Frame(self)
        top_frame.pack(fill=tk.X, pady=5)

        tk.Button(top_frame, text="Start", command=self._handle_start).pack(
            side=tk.LEFT, padx=5
        )
        tk.Button(top_frame, text="Stop", command=self._handle_stop).pack(
            side=tk.LEFT, padx=5
        )

        actions = tk.Frame(self)
        actions.pack(fill=tk.X, pady=5)
        tk.Button(actions, text="verify_smoke", command=lambda: self._run_tool("verify_smoke.py")).pack(side=tk.LEFT, padx=5)
        tk.Button(actions, text="verify_e2e_qa_loop", command=lambda: self._run_tool("verify_e2e_qa_loop.py")).pack(side=tk.LEFT, padx=5)
        tk.Button(actions, text="verify_cooldown", command=lambda: self._run_tool("verify_cooldown.py")).pack(side=tk.LEFT, padx=5)

        self.status_text = tk.Text(self, height=10, wrap=tk.NONE)
        self.status_text.pack(fill=tk.BOTH, padx=5, pady=5)

        self.events_text = tk.Text(self, height=20, wrap=tk.NONE)
        self.events_text.pack(fill=tk.BOTH, padx=5, pady=5)

    def _handle_start(self) -> None:
        self._run_supervisor_async("start")

    def _handle_stop(self) -> None:
        self._run_supervisor_async("stop")

    def _run_supervisor_async(self, command: str) -> None:
        threading.Thread(target=self._run_supervisor, args=(command,), daemon=True).start()

    def _run_supervisor(self, command: str) -> None:
        with self._lock:
            proc = run_supervisor_command(command)
        if proc.returncode != 0:
            messagebox.showerror("Supervisor", proc.stderr or proc.stdout)
        else:
            messagebox.showinfo("Supervisor", proc.stdout or "done")

    def _run_tool(self, script_name: str) -> None:
        script_path = ROOT / "tools" / script_name
        def runner() -> None:
            proc = subprocess.run([sys.executable, str(script_path)], cwd=ROOT)
            if proc.returncode != 0:
                messagebox.showerror("Tool", f"{script_name} failed with code {proc.returncode}")
        threading.Thread(target=runner, daemon=True).start()

    def _refresh(self) -> None:
        state_text = load_state_text()
        events_file = latest_events_file()
        events_text = read_text_tail(events_file) if events_file else "(no events file)"
        self.status_text.delete("1.0", tk.END)
        self.status_text.insert(tk.END, state_text)
        self.events_text.delete("1.0", tk.END)
        self.events_text.insert(tk.END, events_text)

    def _start_auto_refresh(self) -> None:
        def loop() -> None:
            while True:
                time.sleep(1.5)
                self.after(0, self._refresh)

        threading.Thread(target=loop, daemon=True).start()


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
