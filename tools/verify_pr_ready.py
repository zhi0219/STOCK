from __future__ import annotations

import argparse
import json
import os
import re
import sys
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

PR_READY_ARTIFACTS = [
    "artifacts/pr_ready.txt",
    "artifacts/pr_ready_gates.log",
    "artifacts/pr_ready_summary.json",
    "artifacts/pr_ready/_latest.txt",
]


@dataclass
class GateSpec:
    name: str
    args: list[str]


@dataclass
class GateResult:
    name: str
    status: str
    returncode: int
    stdout: str
    stderr: str
    command: list[str]


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.getpid()}"


def _build_gate_specs(artifacts_dir: Path) -> list[GateSpec]:
    return [
        GateSpec(
            name="compile_check",
            args=[
                sys.executable,
                "-m",
                "tools.compile_check",
                "--targets",
                "tools",
                "scripts",
                "tests",
                "--artifacts-dir",
                str(artifacts_dir),
            ],
        ),
        GateSpec(
            name="verify_docs_contract",
            args=[
                sys.executable,
                "-m",
                "tools.verify_docs_contract",
                "--artifacts-dir",
                str(artifacts_dir),
            ],
        ),
        GateSpec(
            name="verify_inventory_contract",
            args=[
                sys.executable,
                "-m",
                "tools.verify_inventory_contract",
                "--artifacts-dir",
                str(artifacts_dir),
            ],
        ),
        GateSpec(
            name="verify_foundation",
            args=[
                sys.executable,
                "-m",
                "tools.verify_foundation",
                "--artifacts-dir",
                str(artifacts_dir),
            ],
        ),
        GateSpec(
            name="verify_safe_pull_contract",
            args=[
                sys.executable,
                "-m",
                "tools.verify_safe_pull_contract",
                "--artifacts-dir",
                str(artifacts_dir),
                "--input-dir",
                str(Path("fixtures") / "safe_pull_contract" / "good"),
            ],
        ),
        GateSpec(
            name="verify_consistency",
            args=[
                sys.executable,
                "-m",
                "tools.verify_consistency",
                "--artifacts-dir",
                str(artifacts_dir),
            ],
        ),
    ]


def _parse_consistency_status(text: str) -> str | None:
    match = re.search(r"CONSISTENCY_SUMMARY\|status=([A-Z]+)", text)
    if not match:
        return None
    return match.group(1)


def _gate_status_from_consistency(result: GateResult) -> str:
    combined = f"{result.stdout}\n{result.stderr}".strip()
    summary_status = _parse_consistency_status(combined)
    if summary_status in {"PASS", "DEGRADED"}:
        return summary_status
    if summary_status == "FAIL":
        return "FAIL"
    return "FAIL"


def _write_text(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def run_gate(spec: GateSpec, cwd: Path) -> GateResult:
    if run_cmd_utf8 is None:  # pragma: no cover - safety net
        raise RuntimeError("run_cmd_utf8 helper unavailable")

    proc = run_cmd_utf8(spec.args, cwd=cwd)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    status = "PASS" if proc.returncode == 0 else "FAIL"
    result = GateResult(
        name=spec.name,
        status=status,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        command=spec.args,
    )
    if spec.name == "verify_consistency":
        result.status = _gate_status_from_consistency(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run PR-ready gates in order.")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory.",
    )
    args = parser.parse_args(argv)

    if configure_stdio_utf8:
        try:
            configure_stdio_utf8()
        except Exception:
            pass

    artifacts_root = args.artifacts_dir.resolve()
    run_dir = artifacts_root / "pr_ready" / _run_id()
    run_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_root / "pr_ready").mkdir(parents=True, exist_ok=True)
    latest_rel = "artifacts/pr_ready/_latest.txt"
    latest_path = artifacts_root / "pr_ready" / "_latest.txt"
    _write_text(latest_path, [run_dir.as_posix()])
    print(f"PR_READY_RUN|run_dir={run_dir.as_posix()}")

    gates = _build_gate_specs(run_dir)

    print("PR_READY_START")
    results: list[GateResult] = []
    log_lines: list[str] = []

    for spec in gates:
        result = run_gate(spec, ROOT)
        results.append(result)
        print(
            "|".join(
                [
                    f"PR_READY_GATE|name={result.name}",
                    f"status={result.status}",
                    f"exit={result.returncode}",
                ]
            )
        )
        log_lines.append(f"PR_READY_CMD|name={result.name}|cmd={' '.join(result.command)}")
        if result.stdout:
            log_lines.append(f"PR_READY_STDOUT|name={result.name}")
            log_lines.append(result.stdout.rstrip())
        if result.stderr:
            log_lines.append(f"PR_READY_STDERR|name={result.name}")
            log_lines.append(result.stderr.rstrip())

    failed = sum(1 for result in results if result.status == "FAIL")
    degraded = sum(1 for result in results if result.status == "DEGRADED")
    if failed:
        summary_status = "FAIL"
        next_action = f"inspect {run_dir / 'pr_ready_gates.log'}"
    elif degraded:
        summary_status = "DEGRADED"
        next_action = "none"
    else:
        summary_status = "PASS"
        next_action = "none"

    print(
        f"PR_READY_SUMMARY|status={summary_status}|failed={failed}|degraded={degraded}|next={next_action}"
    )
    print("PR_READY_END")

    summary_payload = {
        "status": summary_status,
        "failed": failed,
        "degraded": degraded,
        "ts_utc": _ts_utc(),
        "next": next_action,
        "gates": [
            {
                "name": result.name,
                "status": result.status,
                "exit": result.returncode,
                "command": result.command,
            }
            for result in results
        ],
    }
    summary_payload["run_dir"] = run_dir.as_posix()
    summary_payload["latest_pointer"] = latest_rel
    (artifacts_root / "pr_ready_summary.json").parent.mkdir(parents=True, exist_ok=True)
    (artifacts_root / "pr_ready_summary.json").write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_text(
        artifacts_root / "pr_ready.txt",
        [
            f"PR_READY_SUMMARY|status={summary_status}|failed={failed}|degraded={degraded}|next={next_action}",
            f"PR_READY_RUN|run_dir={run_dir.as_posix()}",
            *[
                f"PR_READY_GATE|name={result.name}|status={result.status}|exit={result.returncode}"
                for result in results
            ],
        ],
    )
    run_log_path = run_dir / "pr_ready_gates.log"
    _write_text(run_log_path, log_lines)
    _write_text(
        artifacts_root / "pr_ready_gates.log",
        [
            f"PR_READY_RUN|run_dir={run_dir.as_posix()}",
            f"PR_READY_GATES_LOG|path={run_log_path.as_posix()}",
        ],
    )

    return 0 if summary_status in {"PASS", "DEGRADED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
