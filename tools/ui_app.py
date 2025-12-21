from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List

try:
    import tkinter as tk
    from tkinter import messagebox
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText
except Exception:
    print("tkinter is required to run this UI. Please ensure Tk is installed.")
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "Logs" / "supervisor_state.json"
SUPERVISOR_SCRIPT = ROOT / "tools" / "supervisor.py"
QA_FLOW_SCRIPT = ROOT / "tools" / "qa_flow.py"
CAPTURE_ANSWER_SCRIPT = ROOT / "tools" / "capture_ai_answer.py"
UI_LOG_PATH = ROOT / "Logs" / "ui_actions.log"
KILL_SWITCH = ROOT / "Data" / "KILL_SWITCH"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tools import explain_now
except Exception:
    explain_now = None


def _utf8_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


def read_text_tail(path: Path, lines: int = 20) -> str:
    if not path or not path.exists():
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
        env=_utf8_env(),
    )


@dataclass
class RunResult:
    command: List[str]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str
    note: str = ""

    def format_lines(self) -> str:
        lines = [
            f"Command: {' '.join(self.command)}",
            f"CWD: {self.cwd}",
            f"Exit code: {self.returncode}",
            "--- stdout ---",
            (self.stdout or "(empty)").rstrip(),
            "--- stderr ---",
            (self.stderr or "(empty)").rstrip(),
        ]
        if self.note:
            lines.append(f"Note: {self.note}")
        return "\n".join(lines)


def _append_ui_log(content: str) -> None:
    UI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with UI_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {content}\n")


def run_verify_script(script_name: str) -> RunResult:
    script_path = ROOT / "tools" / script_name
    note = ""
    if KILL_SWITCH.exists():
        try:
            KILL_SWITCH.unlink()
            note = "Removed stale KILL_SWITCH before verify"
        except Exception as exc:
            note = f"KILL_SWITCH present and could not be removed: {exc}"

    command = [sys.executable, str(script_path)]
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=_utf8_env())
    stdout = proc.stdout or ""
    if note:
        stdout = f"{note}\n{stdout}" if stdout else note
    result = RunResult(command, ROOT, proc.returncode, stdout, proc.stderr or "", note)
    _append_ui_log(result.format_lines())
    return result


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
        width = int(self.winfo_screenwidth() * 0.9)
        height = int(self.winfo_screenheight() * 0.9)
        self.geometry(f"{width}x{height}")
        self._lock = threading.Lock()
        self._qa_output_queue: "queue.Queue[str]" = queue.Queue()
        self._verify_output_queue: "queue.Queue[str]" = queue.Queue()
        self._summary_queue: "queue.Queue[str]" = queue.Queue()
        self.last_packet_path: Path | None = None
        self.last_answer_path: Path | None = None
        self._build_ui()
        self._start_auto_refresh()

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True)

        self.run_tab = tk.Frame(notebook)
        self.health_tab = tk.Frame(notebook)
        self.events_tab = tk.Frame(notebook)
        self.summary_tab = tk.Frame(notebook)
        self.qa_tab = tk.Frame(notebook)
        self.verify_tab = tk.Frame(notebook)

        notebook.add(self.run_tab, text="Run")
        notebook.add(self.health_tab, text="Health")
        notebook.add(self.events_tab, text="Events")
        notebook.add(self.summary_tab, text="摘要")
        notebook.add(self.qa_tab, text="AI Q&A")
        notebook.add(self.verify_tab, text="Verify")

        self._build_run_tab()
        self._build_health_tab()
        self._build_events_tab()
        self._build_summary_tab()
        self._build_qa_panel()
        self._build_verify_tab()

        self.after(300, self._drain_qa_output)
        self.after(400, self._drain_verify_output)
        self.after(500, self._drain_summary_queue)

    def _handle_start(self) -> None:
        self._run_supervisor_async("start")

    def _handle_stop(self) -> None:
        self._run_supervisor_async("stop")

    def _run_supervisor_async(self, command: str) -> None:
        threading.Thread(target=self._run_supervisor, args=(command,), daemon=True).start()

    def _run_supervisor(self, command: str) -> None:
        with self._lock:
            proc = run_supervisor_command(command)
        result = RunResult(
            command=[sys.executable, str(SUPERVISOR_SCRIPT), command],
            cwd=ROOT,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
        self._verify_output_queue.put(result.format_lines())
        if proc.returncode != 0:
            self._enqueue_ui(lambda: messagebox.showerror("Supervisor", proc.stderr or proc.stdout))
        else:
            self._enqueue_ui(lambda: messagebox.showinfo("Supervisor", proc.stdout or "done"))
        self._log_run(result.format_lines())

    def _run_tool(self, script_name: str) -> None:
        def runner() -> None:
            result = run_verify_script(script_name)
            self._verify_output_queue.put(result.format_lines())
            if result.returncode != 0:
                self._enqueue_ui(
                    lambda: messagebox.showerror(
                        "Verify", f"{script_name} failed with code {result.returncode}"
                    )
                )
        threading.Thread(target=runner, daemon=True).start()

    def _build_qa_panel(self) -> None:
        panel = tk.LabelFrame(self.qa_tab, text="AI Q&A", padx=5, pady=5)
        panel.pack(fill=tk.BOTH, padx=5, pady=5, expand=True)

        question_frame = tk.Frame(panel)
        question_frame.pack(fill=tk.X, pady=2)
        tk.Label(question_frame, text="Question:").pack(side=tk.LEFT)
        self.question_var = tk.StringVar()
        tk.Entry(question_frame, textvariable=self.question_var, width=80).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        tk.Button(question_frame, text="Generate Q&A Packet", command=self._handle_generate_packet).pack(side=tk.LEFT, padx=5)

        copy_frame = tk.Frame(panel)
        copy_frame.pack(fill=tk.X, pady=2)
        self.packet_path_var = tk.StringVar(value="Last packet: (none)")
        tk.Label(copy_frame, textvariable=self.packet_path_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(copy_frame, text="Copy Packet to Clipboard", command=self._copy_packet).pack(side=tk.LEFT, padx=5)
        tk.Button(copy_frame, text="Open output folder", command=self._open_output_folder).pack(side=tk.LEFT, padx=5)

        instruction = tk.Label(panel, text="Next: paste the AI packet into ChatGPT, then paste the answer below", fg="gray")
        instruction.pack(anchor="w")

        answer_frame = tk.Frame(panel)
        answer_frame.pack(fill=tk.X, pady=2)
        tk.Label(answer_frame, text="Answer (paste from ChatGPT):").pack(anchor="w")
        self.answer_text = tk.Text(panel, height=8, wrap=tk.WORD)
        self.answer_text.pack(fill=tk.BOTH, padx=2, pady=2)

        strict_frame = tk.Frame(panel)
        strict_frame.pack(fill=tk.X, pady=2)
        self.strict_var = tk.BooleanVar(value=False)
        tk.Checkbutton(strict_frame, text="Strict mode (reject trade advice)", variable=self.strict_var).pack(side=tk.LEFT)
        tk.Button(strict_frame, text="Import Answer", command=self._handle_import_answer).pack(side=tk.LEFT, padx=5)

        self.answer_status_var = tk.StringVar(value="Last answer: (none)")
        tk.Label(panel, textvariable=self.answer_status_var, anchor="w").pack(fill=tk.X)

        self.qa_log = ScrolledText(panel, height=10, wrap=tk.WORD)
        self.qa_log.pack(fill=tk.BOTH, padx=2, pady=2, expand=True)

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.qa_log.insert(tk.END, f"[{timestamp}] {message}\n")
        self.qa_log.see(tk.END)

    def _log_run(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.run_log.configure(state=tk.NORMAL)
        self.run_log.insert(tk.END, f"[{timestamp}] {message}\n\n")
        self.run_log.see(tk.END)
        self.run_log.configure(state=tk.DISABLED)

    def _enqueue_ui(self, func) -> None:
        self.after(0, func)

    def _refresh(self) -> None:
        state_text = load_state_text()
        events_file = latest_events_file()
        events_text = read_text_tail(events_file) if events_file else "(no events file)"
        self.status_text.configure(state=tk.NORMAL)
        self.status_text.delete("1.0", tk.END)
        self.status_text.insert(tk.END, state_text)
        self.status_text.configure(state=tk.DISABLED)

        self.events_text.configure(state=tk.NORMAL)
        self.events_text.delete("1.0", tk.END)
        self.events_text.insert(tk.END, events_text)
        self.events_text.configure(state=tk.DISABLED)

    def _handle_generate_packet(self) -> None:
        question = self.question_var.get().strip()
        if not question:
            messagebox.showwarning("AI Q&A", "Please enter a question first")
            return
        self._log(f"Generating packet for question: {question}")
        threading.Thread(target=self._run_generate_packet, args=(question,), daemon=True).start()

    def _handle_import_answer(self) -> None:
        if self.last_packet_path is None:
            messagebox.showwarning("AI Q&A", "Generate a packet first")
            return
        answer = self.answer_text.get("1.0", tk.END).strip()
        if not answer:
            messagebox.showwarning("AI Q&A", "Paste an answer first")
            return
        strict = self.strict_var.get()
        self._log("Importing answer with strict mode %s" % ("ON" if strict else "OFF"))
        threading.Thread(
            target=self._run_import_answer,
            args=(self.last_packet_path, answer, strict),
            daemon=True,
        ).start()

    def _parse_packet_paths(self, stdout: str) -> tuple[Path | None, Path | None]:
        packet_path: Path | None = None
        evidence_path: Path | None = None
        for line in stdout.splitlines():
            if line.startswith("OUTPUT_PACKET="):
                packet_path = Path(line.split("OUTPUT_PACKET=", 1)[1].strip())
            elif line.startswith("OUTPUT_EVIDENCE_PACK="):
                evidence_path = Path(line.split("OUTPUT_EVIDENCE_PACK=", 1)[1].strip())
            elif "AI packet:" in line:
                try:
                    packet_path = Path(line.split("AI packet:", 1)[1].strip())
                except Exception:
                    pass
            elif "Evidence pack:" in line:
                try:
                    evidence_path = Path(line.split("Evidence pack:", 1)[1].strip())
                except Exception:
                    pass
        return packet_path, evidence_path

    def _run_generate_packet(self, question: str) -> None:
        cmd = [sys.executable, str(QA_FLOW_SCRIPT), "--question", question]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=_utf8_env())
        output = proc.stdout or ""
        error_output = proc.stderr or ""
        self._qa_output_queue.put(output)
        if error_output:
            self._qa_output_queue.put(error_output)
        packet_path, evidence_path = self._parse_packet_paths(output)
        if proc.returncode != 0:
            message = f"qa_flow exited with {proc.returncode}"
            if error_output:
                message += f"\n{error_output}"
            self._qa_output_queue.put(message)
            return
        if packet_path:
            self.last_packet_path = packet_path
            self._enqueue_ui(lambda: self.packet_path_var.set(f"Last packet: {packet_path}"))
            self._enqueue_ui(lambda: self._log(f"Packet created at {packet_path}"))
        else:
            self._enqueue_ui(lambda: self._log("Packet path not detected; check logs"))
        if evidence_path:
            self._enqueue_ui(lambda: self._log(f"Evidence pack at {evidence_path}"))

    def _parse_import_output(self, stdout: str) -> tuple[Path | None, Path | None, str]:
        answer_path: Path | None = None
        events_path: Path | None = None
        quality_summary = ""
        for line in stdout.splitlines():
            if line.startswith("Saved answer to:"):
                try:
                    answer_path = Path(line.split(":", 1)[1].strip())
                except Exception:
                    pass
            elif line.startswith("Appended event to:"):
                try:
                    events_path = Path(line.split(":", 1)[1].strip())
                except Exception:
                    pass
            elif line.startswith("Quality:"):
                quality_summary = line
        return answer_path, events_path, quality_summary

    def _run_import_answer(self, packet_path: Path, answer: str, strict: bool) -> None:
        cmd = [
            sys.executable,
            str(CAPTURE_ANSWER_SCRIPT),
            "--packet",
            str(packet_path),
            "--answer-text",
            answer,
        ]
        if strict:
            cmd.append("--strict")
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=_utf8_env())
        output = proc.stdout or ""
        error_output = proc.stderr or ""
        if output:
            self._qa_output_queue.put(output)
        if error_output:
            self._qa_output_queue.put(error_output)
        answer_path, events_path, quality_summary = self._parse_import_output(output)
        if answer_path:
            self.last_answer_path = answer_path
            self._enqueue_ui(lambda: self.answer_status_var.set(f"Last answer: {answer_path}"))
        if events_path:
            self._enqueue_ui(lambda: self._log(f"AI_ANSWER appended to {events_path}"))
        if quality_summary:
            self._enqueue_ui(lambda: self._log(quality_summary))

        if proc.returncode == 2:
            self._enqueue_ui(
                lambda: messagebox.showwarning(
                    "AI Q&A",
                    "Strict mode rejected the answer. Please ask ChatGPT to remove trade advice and add citations.",
                )
            )
        elif proc.returncode != 0:
            self._enqueue_ui(
                lambda: messagebox.showerror(
                    "AI Q&A",
                    f"capture_ai_answer failed with code {proc.returncode}\n{error_output}",
                )
            )
        else:
            self._enqueue_ui(lambda: self._log("Answer imported successfully"))

    def _copy_packet(self) -> None:
        if not self.last_packet_path or not self.last_packet_path.exists():
            messagebox.showwarning("AI Q&A", "No packet to copy yet")
            return
        content = self.last_packet_path.read_text(encoding="utf-8")
        self.clipboard_clear()
        self.clipboard_append(content)
        self._log("Packet copied to clipboard")

    def _copy_summary(self) -> None:
        summary = self.summary_text.get("1.0", tk.END).strip()
        if not summary:
            messagebox.showinfo("摘要", "暂无摘要内容")
            return
        self.clipboard_clear()
        self.clipboard_append(summary)
        messagebox.showinfo("摘要", "摘要已复制")

    def _open_output_folder(self) -> None:
        target = None
        if self.last_answer_path:
            target = self.last_answer_path.parent
        elif self.last_packet_path:
            target = self.last_packet_path.parent
        if target is None:
            messagebox.showinfo("AI Q&A", "No packet or answer yet")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(target)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("AI Q&A", f"Failed to open folder: {exc}")

    def _drain_qa_output(self) -> None:
        while True:
            try:
                message = self._qa_output_queue.get_nowait()
            except queue.Empty:
                break
            self._log(message)
        self.after(500, self._drain_qa_output)

    def _drain_verify_output(self) -> None:
        while True:
            try:
                message = self._verify_output_queue.get_nowait()
            except queue.Empty:
                break
            self.verify_output.configure(state=tk.NORMAL)
            self.verify_output.insert(tk.END, message + "\n\n")
            self.verify_output.see(tk.END)
            self.verify_output.configure(state=tk.DISABLED)
        self.after(500, self._drain_verify_output)

    def _drain_summary_queue(self) -> None:
        updated = False
        while True:
            try:
                message = self._summary_queue.get_nowait()
            except queue.Empty:
                break
            self.summary_text.configure(state=tk.NORMAL)
            self.summary_text.delete("1.0", tk.END)
            self.summary_text.insert(tk.END, message)
            self.summary_text.configure(state=tk.DISABLED)
            updated = True
        if updated:
            self.summary_text.see(tk.END)
        self.after(1000, self._drain_summary_queue)

    def _start_auto_refresh(self) -> None:
        def loop() -> None:
            while True:
                time.sleep(1.5)
                self.after(0, self._refresh)

        threading.Thread(target=loop, daemon=True).start()

        def summary_loop() -> None:
            while True:
                time.sleep(2)
                if explain_now:
                    try:
                        summary = explain_now.generate_summary()
                    except Exception as exc:  # pragma: no cover - UI feedback
                        summary = f"无法生成摘要: {exc}"
                else:
                    summary = "摘要模块不可用"
                self._summary_queue.put(summary)

        threading.Thread(target=summary_loop, daemon=True).start()

    def _build_run_tab(self) -> None:
        top_frame = tk.Frame(self.run_tab)
        top_frame.pack(fill=tk.X, pady=5)

        tk.Button(top_frame, text="Start", command=self._handle_start).pack(
            side=tk.LEFT, padx=5
        )
        tk.Button(top_frame, text="Stop", command=self._handle_stop).pack(
            side=tk.LEFT, padx=5
        )

        self.run_log = ScrolledText(self.run_tab, height=10, wrap=tk.WORD)
        self.run_log.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.run_log.configure(state=tk.DISABLED)

    def _build_health_tab(self) -> None:
        self.status_text = ScrolledText(self.health_tab, wrap=tk.WORD)
        self.status_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.status_text.configure(state=tk.DISABLED)

    def _build_events_tab(self) -> None:
        self.events_text = ScrolledText(self.events_tab, wrap=tk.WORD)
        self.events_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.events_text.configure(state=tk.DISABLED)

    def _build_summary_tab(self) -> None:
        summary_frame = tk.Frame(self.summary_tab)
        summary_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        button_frame = tk.Frame(summary_frame)
        button_frame.pack(fill=tk.X, pady=2)
        tk.Button(button_frame, text="复制摘要", command=self._copy_summary).pack(
            side=tk.RIGHT, padx=5
        )
        self.summary_text = ScrolledText(summary_frame, wrap=tk.WORD)
        self.summary_text.pack(fill=tk.BOTH, expand=True)
        self.summary_text.configure(state=tk.DISABLED)

    def _build_verify_tab(self) -> None:
        button_frame = tk.Frame(self.verify_tab)
        button_frame.pack(fill=tk.X, pady=5)
        tk.Button(
            button_frame,
            text="verify_smoke",
            command=lambda: self._run_tool("verify_smoke.py"),
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            button_frame,
            text="verify_e2e_qa_loop",
            command=lambda: self._run_tool("verify_e2e_qa_loop.py"),
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            button_frame,
            text="verify_ui_actions",
            command=lambda: self._run_tool("verify_ui_actions.py"),
        ).pack(side=tk.LEFT, padx=5)

        self.verify_output = ScrolledText(self.verify_tab, wrap=tk.WORD)
        self.verify_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.verify_output.configure(state=tk.DISABLED)


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
