from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
SUMMARY_TAG = "PR21_GATE_SUMMARY"
PY_PICKER = TOOLS_DIR / "run_py.py"

SCRIPTS = [
    "verify_repo_hygiene.py",
    "verify_consistency.py",
    "verify_kill_switch_recovery.py",
    "verify_run_completeness_contract.py",
    "verify_latest_artifacts.py",
]


def _pick_python() -> tuple[str, int, int, str]:
    if PY_PICKER.exists():
        proc = subprocess.run(
            [sys.executable, str(PY_PICKER)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (proc.stdout or "").strip()
        if output.startswith("PY_PICK|"):
            parts = dict(item.split("=", 1) for item in output.split("|")[1:] if "=" in item)
            path = parts.get("path") or sys.executable
            using_venv = int(parts.get("using_venv", "0"))
            degraded = int(parts.get("degraded", "0"))
            reason = parts.get("reason", "unknown")
            print(output)
            return path, using_venv, degraded, reason
    path = sys.executable
    using_venv = 1 if ".venv" in Path(path).parts else 0
    degraded = 0 if using_venv else 1
    reason = "sys_executable"
    print(f"PY_PICK|path={path}|using_venv={using_venv}|degraded={degraded}|reason={reason}")
    return path, using_venv, degraded, reason


def _run(script: str, python_exec: str) -> tuple[int, str]:
    path = TOOLS_DIR / script
    if not path.exists():
        return 1, f"missing:{script}"
    proc = subprocess.run(
        [python_exec, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output.strip()


def main() -> int:
    print("PR21_GATE_START")
    status = "PASS"
    degraded: list[str] = []
    details: list[str] = []
    python_exec, using_venv, degraded_flag, degraded_reason = _pick_python()
    if degraded_flag:
        degraded.append(f"venv_unavailable:{degraded_reason}")

    for script in SCRIPTS:
        rc, output = _run(script, python_exec)
        details.append(f"{script}:rc={rc}")
        if rc != 0:
            status = "FAIL"
            degraded.append(script)
        if output:
            print(output)

    summary = "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"using_venv={using_venv}",
            f"degraded={1 if degraded else 0}",
            f"degraded_reasons={','.join(degraded) if degraded else 'none'}",
            f"details={';'.join(details)}",
        ]
    )
    print(summary)
    print("PR21_GATE_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
