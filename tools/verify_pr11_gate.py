from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from tools.git_baseline_probe import probe_baseline

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
LOGS_DIR = ROOT / "Logs"
SUMMARY_TAG = "PR11_GATE_SUMMARY"
TARGETS = [
    TOOLS_DIR / "progress_judge.py",
    TOOLS_DIR / "verify_progress_truth.py",
    TOOLS_DIR / "verify_pr11_gate.py",
]


def _summary_line(
    status: str,
    reasons: list[str],
    using_venv: bool,
    repo_root_ok: bool,
    can_write_logs: bool,
    baseline: str,
    baseline_status: str,
    baseline_details: str,
) -> str:
    detail = ";".join(reasons) if reasons else "ok"
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"using_venv={int(using_venv)}",
            f"repo_root_ok={int(repo_root_ok)}",
            f"can_write_logs={int(can_write_logs)}",
            f"baseline={baseline}",
            f"baseline_status={baseline_status}",
            f"baseline_details={baseline_details}",
            f"reasons={detail}",
        ]
    )


def _check_venv() -> tuple[bool, bool]:
    windows = ROOT / ".venv" / "Scripts" / "python.exe"
    posix = ROOT / ".venv" / "bin" / "python"
    venv_present = windows.exists() or posix.exists()
    exe_path = Path(sys.executable)
    prefix_path = Path(sys.prefix)
    using_venv = venv_present and (".venv" in str(exe_path) or ".venv" in str(prefix_path))
    return venv_present, using_venv


def _check_repo_root() -> bool:
    try:
        return Path.cwd().resolve() == ROOT
    except Exception:
        return False


def _probe_logs() -> tuple[bool, str]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(dir=LOGS_DIR, delete=True) as fh:
            fh.write(b"ok")
            fh.flush()
        return True, ""
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)


def _run_py_compile() -> tuple[int, str]:
    cmd = [sys.executable, "-m", "py_compile", *[str(t) for t in TARGETS]]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output.strip()


def _run_progress_truth() -> tuple[int, str]:
    cmd = [sys.executable, str(TOOLS_DIR / "verify_progress_truth.py")]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output.strip()


def main() -> int:
    reasons: list[str] = []
    venv_present, using_venv = _check_venv()
    repo_root_ok = _check_repo_root()
    can_write_logs, log_error = _probe_logs()
    baseline_info = probe_baseline()
    baseline = baseline_info.get("baseline") or "unavailable"
    baseline_status = baseline_info.get("status") or "UNAVAILABLE"
    baseline_details = baseline_info.get("details") or "unknown"

    if not repo_root_ok:
        reasons.append("run_from_repo_root")
    if not venv_present:
        reasons.append("venv_missing")
    if venv_present and not using_venv:
        reasons.append("not_using_venv")
    if not can_write_logs:
        reasons.append(f"logs_not_writable:{log_error}")

    compile_rc = None
    compile_output = ""
    truth_rc = None
    truth_output = ""
    if not reasons:
        compile_rc, compile_output = _run_py_compile()
        if compile_rc != 0:
            reasons.append("py_compile_failed")
        truth_rc, truth_output = _run_progress_truth()
        if truth_rc != 0:
            reasons.append("verify_progress_truth_failed")

    status = "PASS" if not reasons else "FAIL"
    summary = _summary_line(
        status, reasons, using_venv, repo_root_ok, can_write_logs, baseline, baseline_status, baseline_details
    )

    print(summary)
    if compile_output:
        print("PY_COMPILE_OUTPUT_START")
        print(compile_output)
        print("PY_COMPILE_OUTPUT_END")
    if truth_output:
        print("VERIFY_PROGRESS_TRUTH_OUTPUT_START")
        print(truth_output)
        print("VERIFY_PROGRESS_TRUTH_OUTPUT_END")
    if reasons:
        print("REASONS:")
        for reason in reasons:
            print(f"- {reason}")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
