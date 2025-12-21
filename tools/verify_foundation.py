from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tools.stdio_utf8 import configure_stdio_utf8, run_cmd_utf8
except Exception:  # pragma: no cover - fallback for import edge cases
    configure_stdio_utf8 = None  # type: ignore[assignment]
    run_cmd_utf8 = None  # type: ignore[assignment]


@dataclass
class GateResult:
    name: str
    status: str
    returncode: int
    stdout: str
    stderr: str
    degraded: bool = False


GATES = [
    "verify_consistency.py",
    "verify_smoke.py",
    "verify_sim_safety_pack.py",
    "verify_no_lookahead_sim.py",
]


def _detect_degraded(text: str) -> bool:
    probe = text.upper()
    return "DEGRADED" in probe or "SKIP" in probe


def run_gate(script_name: str) -> GateResult:
    script_path = ROOT / "tools" / script_name
    if run_cmd_utf8 is None:  # pragma: no cover - safety net
        raise RuntimeError("run_cmd_utf8 helper unavailable")

    proc = run_cmd_utf8([sys.executable, str(script_path)], cwd=ROOT)
    combined = (proc.stdout or "") + (proc.stderr or "")
    degraded = proc.returncode == 0 and _detect_degraded(combined)
    status = "PASS" if proc.returncode == 0 else "FAIL"
    if degraded:
        status = "DEGRADED"

    return GateResult(
        name=script_name,
        status=status,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        degraded=degraded,
    )


def main() -> int:
    """Run all foundation gates and return a consolidated exit code.

    Exit semantics:
    - Any gate returning a failure (non-zero exit code) makes the process exit 1.
    - DEGRADED/SKIP cases keep exit code 0 and are surfaced via the summary flag.
    - A clean run with no failures returns 0.
    The marker lines bound the output so downstream tools can parse the block.
    """
    if configure_stdio_utf8:
        try:
            configure_stdio_utf8()
        except Exception:
            pass

    print("===FOUNDATION_GATES_START===")
    results: list[GateResult] = []
    for gate in GATES:
        result = run_gate(gate)
        results.append(result)
        print(
            f"GATE_RESULT|name={result.name}|status={result.status}|exit={result.returncode}"
        )
        if result.stdout:
            print(f"--- {result.name} stdout ---")
            print(result.stdout.rstrip())
        if result.stderr:
            print(f"--- {result.name} stderr ---")
            print(result.stderr.rstrip())

    has_failures = any(r.status == "FAIL" for r in results)
    degraded = any(r.degraded for r in results)
    summary_status = "FAIL" if has_failures else "PASS"
    print(
        f"FOUNDATION_SUMMARY|status={summary_status}|degraded={int(degraded)}|failed={int(has_failures)}"
    )
    print("===FOUNDATION_GATES_END===")

    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
