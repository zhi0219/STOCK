from __future__ import annotations

import os
import queue
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
QA_FLOW_SCRIPT = ROOT / "tools" / "qa_flow.py"
CAPTURE_ANSWER_SCRIPT = ROOT / "tools" / "capture_ai_answer.py"


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
        self.geometry("1000x800")
        self._lock = threading.Lock()
        self._qa_output_queue: "queue.Queue[str]" = queue.Queue()
        self.last_packet_path: Path | None = None
        self.last_answer_path: Path | None = None
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

        self._build_qa_panel()

        self.status_text = tk.Text(self, height=10, wrap=tk.NONE)
        self.status_text.pack(fill=tk.BOTH, padx=5, pady=5)

        self.events_text = tk.Text(self, height=20, wrap=tk.NONE)
        self.events_text.pack(fill=tk.BOTH, padx=5, pady=5)

        self.after(300, self._drain_qa_output)

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

    def _build_qa_panel(self) -> None:
        panel = tk.LabelFrame(self, text="AI Q&A", padx=5, pady=5)
        panel.pack(fill=tk.BOTH, padx=5, pady=5)

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

        self.qa_log = tk.Text(panel, height=10, wrap=tk.WORD)
        self.qa_log.pack(fill=tk.BOTH, padx=2, pady=2)

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.qa_log.insert(tk.END, f"[{timestamp}] {message}\n")
        self.qa_log.see(tk.END)

    def _enqueue_ui(self, func) -> None:
        self.after(0, func)

    def _refresh(self) -> None:
        state_text = load_state_text()
        events_file = latest_events_file()
        events_text = read_text_tail(events_file) if events_file else "(no events file)"
        self.status_text.delete("1.0", tk.END)
        self.status_text.insert(tk.END, state_text)
        self.events_text.delete("1.0", tk.END)
        self.events_text.insert(tk.END, events_text)

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
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
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
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
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
                subprocess.Popen(["xdg-open", str(target)])
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
