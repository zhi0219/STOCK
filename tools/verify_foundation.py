from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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
    reason: str | None = None


GATES = [
    "verify_consistency.py",
    "verify_smoke.py",
    "verify_sim_safety_pack.py",
    "verify_no_lookahead_sim.py",
]

OPTIONAL_DEPS = ("pandas", "yaml", "yfinance")


def _detect_degraded(text: str) -> bool:
    probe = text.upper()
    return "DEGRADED" in probe or "SKIP" in probe


def _detect_missing_optional_deps(text: str) -> list[str]:
    probe = text.lower()
    missing: list[str] = []
    for dep in OPTIONAL_DEPS:
        # Normalize common module-not-found messages.
        if f"no module named '{dep.lower()}'" in probe or f"missing optional dependency '{dep.lower()}'" in probe:
            missing.append(dep)
    return missing


def _format_missing(deps: Iterable[str]) -> str:
    parts = list(deps)
    return "missing_deps=" + ",".join(parts) if parts else ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _write_text_utf8_nobom_lf(path: Path, lines: Iterable[str]) -> None:
    text = "\n".join(lines) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _write_json(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(data + "\n")
    tmp_path.replace(path)


def _exception_payload(error: BaseException) -> dict:
    return {
        "ts_utc": _utc_now_iso(),
        "type": type(error).__name__,
        "message": str(error),
        "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
    }


def run_gate(script_name: str) -> GateResult:
    script_path = ROOT / "tools" / script_name
    if run_cmd_utf8 is None:  # pragma: no cover - safety net
        raise RuntimeError("run_cmd_utf8 helper unavailable")

    proc = run_cmd_utf8([sys.executable, str(script_path)], cwd=ROOT)
    combined = (proc.stdout or "") + (proc.stderr or "")
    missing_deps = _detect_missing_optional_deps(combined)
    has_degraded_marker = _detect_degraded(combined)

    status = "PASS"
    reason = None
    degraded = False

    if missing_deps:
        status = "DEGRADED"
        degraded = True
        reason = _format_missing(missing_deps)
    elif proc.returncode != 0 and has_degraded_marker:
        status = "SKIP"
        degraded = True
        reason = "reported SKIP"
    elif proc.returncode != 0:
        status = "FAIL"
    elif has_degraded_marker:
        status = "DEGRADED"
        degraded = True

    return GateResult(
        name=script_name,
        status=status,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        degraded=degraded,
        reason=reason,
    )


def main(argv: list[str] | None = None) -> int:
    """Run all foundation gates and return a consolidated exit code.

    Exit semantics:
    - Only gates marked FAIL keep the process fail-closed.
    - DEGRADED/SKIP (e.g., missing optional deps or restricted environments) emit
      `degraded=1` in the summary but preserve exit code 0.
    - Marker lines bound the output so downstream tools can parse the block.
    """
    parser = argparse.ArgumentParser(description="Run foundation gates.")
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Artifacts directory (reserved for future use).",
    )
    args = parser.parse_args(argv)

    if configure_stdio_utf8:
        try:
            configure_stdio_utf8()
        except Exception:
            pass

    try:
        artifacts_dir = _ensure_dir(Path(args.artifacts_dir))
    except Exception as exc:
        print(f"FOUNDATION_EVIDENCE_WRITE_FAIL|detail={exc}")
        return 1

    ts_start = _utc_now_iso()
    markers: list[str] = [f"FOUNDATION_START|ts_utc={ts_start}|artifacts_dir={artifacts_dir}"]
    results: list[GateResult] = []
    exception_info: dict | None = None

    print("===FOUNDATION_GATES_START===")
    try:
        for gate in GATES:
            result = run_gate(gate)
            results.append(result)
            markers.append(
                f"FOUNDATION_GATE|name={result.name}|status={result.status}|degraded={int(result.degraded)}"
            )
            print(
                "|".join(
                    part
                    for part in [
                        f"GATE_RESULT|name={result.name}",
                        f"status={result.status}",
                        f"exit={result.returncode}",
                        result.reason if result.reason else None,
                    ]
                    if part is not None
                )
            )
            if result.stdout:
                print(f"--- {result.name} stdout ---")
                print(result.stdout.rstrip())
            if result.stderr:
                print(f"--- {result.name} stderr ---")
                print(result.stderr.rstrip())
    except Exception as exc:
        exception_info = _exception_payload(exc)
        print(f"FOUNDATION_EXCEPTION|type={exception_info['type']}")

    has_failures = any(r.status == "FAIL" for r in results) or exception_info is not None
    degraded = any(r.degraded for r in results)
    summary_status = "FAIL" if has_failures else "PASS"
    summary_line = (
        f"FOUNDATION_SUMMARY|status={summary_status}|degraded={int(degraded)}|failed={int(has_failures)}"
    )
    print(summary_line)
    print("===FOUNDATION_GATES_END===")

    next_step = "review artifacts" if has_failures else "none"
    markers.append(summary_line)
    markers.append(f"FOUNDATION_END|exit_code={int(has_failures)}|next={next_step}")

    summary_payload: dict = {
        "ts_utc": ts_start,
        "cmd": "tools.verify_foundation",
        "artifacts_dir": str(artifacts_dir),
        "summary_status": summary_status,
        "degraded": int(degraded),
        "failed": int(has_failures),
        "results": [
            {
                "name": result.name,
                "status": result.status,
                "degraded": int(result.degraded),
                **({"detail": result.reason} if result.reason else {}),
            }
            for result in results
        ],
        "stdout_summary_line": summary_line,
    }
    if exception_info is not None:
        summary_payload["exception"] = exception_info

    summary_path = artifacts_dir / "foundation_summary.json"
    markers_path = artifacts_dir / "foundation_markers.txt"
    exception_path = artifacts_dir / "foundation_exception.json"

    try:
        _write_json(summary_path, summary_payload)
        _write_text_utf8_nobom_lf(markers_path, markers)
        if exception_info is not None:
            _write_json(exception_path, exception_info)
    except Exception as exc:
        print(f"FOUNDATION_EVIDENCE_WRITE_FAIL|detail={exc}")
        return 1

    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
