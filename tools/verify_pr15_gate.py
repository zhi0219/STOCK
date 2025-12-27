from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
LOGS_DIR = ROOT / "Logs"
REPO_HYGIENE = TOOLS_DIR / "verify_repo_hygiene.py"
BASELINE_PROBE = TOOLS_DIR / "git_baseline_probe.py"
BASELINE_GUIDE = TOOLS_DIR / "baseline_fix_guide.py"
SUMMARY_TAG = "PR15_GATE_SUMMARY"

PY_COMPILE_TARGETS = [
    TOOLS_DIR / "baseline_fix_guide.py",
    TOOLS_DIR / "git_baseline_probe.py",
    TOOLS_DIR / "verify_pr15_gate.py",
    TOOLS_DIR / "verify_consistency.py",
    TOOLS_DIR / "verify_pr14_gate.py",
    TOOLS_DIR / "verify_pr13_gate.py",
    TOOLS_DIR / "verify_pr12_gate.py",
    TOOLS_DIR / "verify_pr11_gate.py",
    TOOLS_DIR / "ui_app.py",
    TOOLS_DIR / "verify_repo_hygiene.py",
]


def _run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _run_py_compile() -> tuple[bool, str]:
    args = [str(path) for path in PY_COMPILE_TARGETS]
    result = _run_cmd([sys.executable, "-m", "py_compile", *args])
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    return result.returncode == 0, output.strip()


def _parse_marker(line: str) -> dict[str, str]:
    parts = line.split("|")
    payload = {}
    for item in parts[1:]:
        if "=" in item:
            key, value = item.split("=", 1)
            payload[key] = value
    return payload


def _find_marker(text: str, prefix: str) -> dict[str, str] | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            return _parse_marker(line)
    return None


def _run_baseline_probe() -> tuple[bool, str, dict[str, str] | None]:
    result = _run_cmd([sys.executable, str(BASELINE_PROBE)])
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    marker = _find_marker(output, "BASELINE_PROBE|")
    ok = result.returncode == 0 and marker is not None
    return ok, output.strip(), marker


def _run_baseline_guide(report_only: bool) -> tuple[bool, str, dict[str, str] | None, bool]:
    output_path = LOGS_DIR / "baseline_guide.txt"
    existed_before = output_path.exists()
    cmd = [sys.executable, str(BASELINE_GUIDE)]
    if report_only:
        cmd.append("--report-only")
    result = _run_cmd(cmd)
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    marker = _find_marker(output, "BASELINE_GUIDE_SUMMARY|")
    ok = result.returncode == 0 and "BASELINE_GUIDE_START" in output and "BASELINE_GUIDE_END" in output and marker
    exists_after = output_path.exists()
    created = (not existed_before) and exists_after
    return bool(ok), output.strip(), marker, created


def _run_repo_hygiene() -> tuple[bool, str]:
    result = _run_cmd([sys.executable, str(REPO_HYGIENE)])
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    return result.returncode == 0, output.strip()


def _summary_line(status: str, degraded: bool, degraded_reasons: list[str], baseline: str) -> str:
    detail = ",".join(degraded_reasons) if degraded_reasons else "none"
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"degraded={int(degraded)}",
            f"degraded_reasons={detail}",
            f"baseline={baseline}",
        ]
    )


def main() -> int:
    status = "PASS"
    reasons: list[str] = []
    degraded_reasons: list[str] = []
    baseline = "unavailable"

    compile_ok, compile_output = _run_py_compile()
    if not compile_ok:
        status = "FAIL"
        reasons.append("py_compile_failed")

    probe_ok, probe_output, probe_marker = _run_baseline_probe()
    if not probe_ok or not probe_marker:
        status = "FAIL"
        reasons.append("baseline_probe_failed")
    else:
        baseline = probe_marker.get("baseline", "unavailable")
        baseline_status = probe_marker.get("status", "UNAVAILABLE")
        baseline_details = probe_marker.get("details", "unknown")
        if baseline_status != "AVAILABLE":
            degraded_reasons.append(f"baseline_unavailable_{baseline_details}")

    guide_ok, guide_output, guide_marker, guide_created = _run_baseline_guide(report_only=True)
    if not guide_ok or not guide_marker:
        status = "FAIL"
        reasons.append("baseline_guide_failed")
    else:
        guide_status = guide_marker.get("status")
        if probe_marker:
            probe_status = probe_marker.get("status")
            if probe_status == "AVAILABLE" and guide_status != "OK":
                status = "FAIL"
                reasons.append("baseline_marker_mismatch")
            if probe_status != "AVAILABLE" and guide_status != "WARN":
                status = "FAIL"
                reasons.append("baseline_marker_mismatch")
            if guide_marker.get("baseline") != probe_marker.get("baseline"):
                status = "FAIL"
                reasons.append("baseline_marker_inconsistent")

    if guide_created:
        status = "FAIL"
        reasons.append("unsafe_filesystem_behavior")

    hygiene_ok, hygiene_output = _run_repo_hygiene()
    if not hygiene_ok:
        status = "FAIL"
        reasons.append("repo_hygiene_failed")

    degraded = bool(degraded_reasons)
    summary = _summary_line(status, degraded, degraded_reasons, baseline)

    print("PR15_GATE_START")
    print(summary)
    if compile_output:
        print("PY_COMPILE_OUTPUT_START")
        print(compile_output)
        print("PY_COMPILE_OUTPUT_END")
    if probe_output:
        print("BASELINE_PROBE_OUTPUT_START")
        print(probe_output)
        print("BASELINE_PROBE_OUTPUT_END")
    if guide_output:
        print("BASELINE_GUIDE_OUTPUT_START")
        print(guide_output)
        print("BASELINE_GUIDE_OUTPUT_END")
    if hygiene_output:
        print("REPO_HYGIENE_OUTPUT_START")
        print(hygiene_output)
        print("REPO_HYGIENE_OUTPUT_END")
    if reasons:
        print("REASONS:")
        for reason in reasons:
            print(f"- {reason}")
    if degraded_reasons:
        print("DEGRADED:")
        for reason in degraded_reasons:
            print(f"- {reason}")
    print(summary)
    print("PR15_GATE_END")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
