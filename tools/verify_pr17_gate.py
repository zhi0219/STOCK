from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
SUMMARY_TAG = "PR17_GATE_SUMMARY"


def _using_venv() -> int:
    exe = str(Path(sys.executable).resolve())
    prefix = str(Path(sys.prefix).resolve())
    return 1 if ".venv" in exe or ".venv" in prefix else 0


def _compile_targets(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path}: {exc.msg}")
    return failures


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _extract_summary(output: str, marker: str) -> dict[str, str]:
    summary_line = ""
    for line in output.splitlines():
        if line.startswith(marker):
            summary_line = line
    if not summary_line:
        return {}
    parts = summary_line.split("|")[1:]
    data: dict[str, str] = {}
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            data[key] = value
    return data


def _summary_line(status: str, degraded: bool, degraded_reasons: list[str], reasons: list[str]) -> str:
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"degraded={1 if degraded else 0}",
            f"degraded_reasons={';'.join(degraded_reasons) if degraded_reasons else 'none'}",
            f"reasons={';'.join(reasons) if reasons else 'ok'}",
            f"using_venv={_using_venv()}",
        ]
    )


def main() -> int:
    status = "PASS"
    reasons: list[str] = []
    degraded = False
    degraded_reasons: list[str] = []

    print("PR17_GATE_START")

    compile_failures = _compile_targets(
        [
            TOOLS_DIR / "ui_app.py",
            TOOLS_DIR / "ui_scroll.py",
            TOOLS_DIR / "verify_ui_scroll.py",
            Path(__file__),
        ]
    )
    if compile_failures:
        status = "FAIL"
        reasons.append("py_compile_failed")
        print("PY_COMPILE_OUTPUT_START")
        for failure in compile_failures:
            print(failure)
        print("PY_COMPILE_OUTPUT_END")

    repo_hygiene = _run([sys.executable, str(TOOLS_DIR / "verify_repo_hygiene.py")])
    if repo_hygiene.stdout:
        print(repo_hygiene.stdout.strip())
    if repo_hygiene.stderr:
        print(repo_hygiene.stderr.strip())
    if repo_hygiene.returncode != 0:
        status = "FAIL"
        reasons.append("repo_hygiene_failed")

    consistency = _run([sys.executable, str(TOOLS_DIR / "verify_consistency.py")])
    if consistency.stdout:
        print(consistency.stdout.strip())
    if consistency.stderr:
        print(consistency.stderr.strip())
    if consistency.returncode != 0:
        status = "FAIL"
        reasons.append("consistency_failed")
    else:
        summary = _extract_summary(consistency.stdout, "CONSISTENCY_SUMMARY")
        if summary.get("status") == "DEGRADED":
            degraded = True
            notes = summary.get("notes", "consistency_degraded")
            degraded_reasons.append(notes)

    ui_scroll = _run([sys.executable, str(TOOLS_DIR / "verify_ui_scroll.py")])
    if ui_scroll.stdout:
        print(ui_scroll.stdout.strip())
    if ui_scroll.stderr:
        print(ui_scroll.stderr.strip())
    ui_scroll_summary = _extract_summary(ui_scroll.stdout, "UI_SCROLL_SUMMARY")
    ui_scroll_status = ui_scroll_summary.get("status")
    if ui_scroll.returncode != 0 or ui_scroll_status == "FAIL":
        status = "FAIL"
        reasons.append("ui_scroll_failed")
    elif ui_scroll_status == "SKIP":
        degraded = True
        degraded_reasons.append("ui_display_unavailable")

    summary = _summary_line(status, degraded, degraded_reasons, reasons)
    print(summary)
    print("PR17_GATE_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
