from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONSISTENCY_SUMMARY_RE = re.compile(r"CONSISTENCY_SUMMARY\\|status=([^|\\s]+)(?:\\|notes=([^|]*))?")
EXCEPTION_RE = re.compile(r"(\\w+(?:Error|Exception)):\\s*(.+)")


@dataclass
class CheckOutcome:
    name: str
    status: str
    reason: str | None
    returncode: int
    output: str


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def _parse_consistency_status(output: str) -> tuple[str | None, str | None]:
    match = CONSISTENCY_SUMMARY_RE.search(output)
    if not match:
        return None, None
    status = match.group(1)
    notes = match.group(2)
    if notes:
        notes = notes.strip()
    return status, notes


def _detect_pr_gate() -> str | None:
    tools_dir = ROOT / "tools"
    pr_gates = list(tools_dir.glob("verify_pr*_gate.py"))
    if not pr_gates:
        return None

    def _gate_key(path: Path) -> tuple[int, str]:
        match = re.search(r"verify_pr(\\d+)_gate", path.stem)
        if match:
            return int(match.group(1)), path.stem
        return -1, path.stem

    best = sorted(pr_gates, key=_gate_key)[-1]
    return f"tools.{best.stem}"


def _detect_canonical_runner() -> str:
    if (ROOT / "tools" / "verify_foundation.py").exists():
        return "tools.verify_foundation"
    pr_gate = _detect_pr_gate()
    if pr_gate:
        return pr_gate
    if (ROOT / "tools" / "verify_consistency.py").exists():
        return "tools.verify_consistency"
    raise FileNotFoundError("No canonical gate runner found.")


def _format_exception(output: str) -> str:
    matches = EXCEPTION_RE.findall(output)
    if not matches:
        return "unknown_exception"
    exc_type, message = matches[-1]
    return f"{exc_type}: {message.strip()}"


def _check_module_consistency() -> CheckOutcome:
    cmd = [sys.executable, "-m", "tools.verify_consistency"]
    rc, output = _run(cmd)
    status, notes = _parse_consistency_status(output)
    if status is None:
        return CheckOutcome("module_verify_consistency", "FAIL", "missing_summary", rc, output)
    if rc != 0 or status == "FAIL":
        return CheckOutcome("module_verify_consistency", "FAIL", f"status={status}", rc, output)
    if status == "DEGRADED":
        reason = f"status={status}"
        if notes and notes != "none":
            reason = f"{reason};notes={notes}"
        return CheckOutcome("module_verify_consistency", "DEGRADED", reason, rc, output)
    return CheckOutcome("module_verify_consistency", "PASS", None, rc, output)


def _check_path_consistency() -> CheckOutcome:
    script_path = ROOT / "tools" / "verify_consistency.py"
    cmd = [sys.executable, str(script_path)]
    rc, output = _run(cmd)
    if "ModuleNotFoundError" in output:
        return CheckOutcome("path_verify_consistency", "FAIL", "ModuleNotFoundError", rc, output)
    status, notes = _parse_consistency_status(output)
    if status is None:
        return CheckOutcome("path_verify_consistency", "FAIL", "missing_summary", rc, output)
    if rc != 0 or status == "FAIL":
        return CheckOutcome("path_verify_consistency", "FAIL", f"status={status}", rc, output)
    if status == "DEGRADED":
        reason = f"status={status}"
        if notes and notes != "none":
            reason = f"{reason};notes={notes}"
        return CheckOutcome("path_verify_consistency", "DEGRADED", reason, rc, output)
    return CheckOutcome("path_verify_consistency", "PASS", None, rc, output)


def _check_module_runner(module: str) -> CheckOutcome:
    cmd = [sys.executable, "-m", module]
    rc, output = _run(cmd)
    if rc != 0:
        reason = f"runner={module};exc={_format_exception(output)}"
        return CheckOutcome("module_runner", "FAIL", reason, rc, output)
    return CheckOutcome("module_runner", "PASS", None, rc, output)


def _check_module_import(module: str, name: str) -> CheckOutcome:
    cmd = [
        sys.executable,
        "-c",
        "import importlib; importlib.import_module('" + module + "')",
    ]
    rc, output = _run(cmd)
    if rc != 0:
        reason = f"module={module};exc={_format_exception(output)}"
        return CheckOutcome(name, "FAIL", reason, rc, output)
    return CheckOutcome(name, "PASS", None, rc, output)


def _format_reasons(results: list[CheckOutcome]) -> str:
    reasons: list[str] = []
    for result in results:
        if result.status == "PASS":
            continue
        if result.reason:
            reasons.append(f"{result.name}:{result.reason}")
        else:
            reasons.append(f"{result.name}:{result.status}")
    return ",".join(reasons) if reasons else "none"


def main() -> int:
    print("IMPORT_CONTRACT_START")
    results: list[CheckOutcome] = []
    summary_status = "FAIL"
    reasons = "none"
    try:
        runner = _detect_canonical_runner()
        results = [
            _check_module_consistency(),
            _check_path_consistency(),
            _check_module_import("tools.verify_pr23_gate", "module_import_pr23_gate"),
            _check_module_import(runner, "module_import_runner"),
            _check_module_runner(runner),
        ]
        has_failures = any(r.status == "FAIL" for r in results)
        degraded = any(r.status == "DEGRADED" for r in results)
        summary_status = "FAIL" if has_failures else "DEGRADED" if degraded else "PASS"
        reasons = _format_reasons(results)
    except Exception as exc:
        results = [CheckOutcome("import_contract", "FAIL", str(exc), 1, "")]
        summary_status = "FAIL"
        reasons = _format_reasons(results)

    print(
        "|".join(
            [
                "IMPORT_CONTRACT_SUMMARY",
                f"status={summary_status}",
                f"reasons={reasons}",
            ]
        )
    )
    print("IMPORT_CONTRACT_END")
    return 1 if summary_status == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
