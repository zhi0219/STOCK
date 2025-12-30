from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.compile_check import run_compile_check
from tools.paths import repo_root, runtime_dir, to_repo_relative

ROOT = repo_root()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _scan_conflict_markers(paths: Iterable[Path]) -> list[dict[str, Any]]:
    markers = ("<<<<<<<", "=======", ">>>>>>>")
    hits: list[dict[str, Any]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if any(marker in line for marker in markers):
                hits.append(
                    {"path": to_repo_relative(path), "line": idx, "line_text": line.strip()}
                )
                break
    return hits


def _tracked_source_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        path = ROOT / line.strip()
        if path.suffix.lower() in {".py", ".sh", ".md", ".ps1"}:
            paths.append(path)
    return paths


def _run_py_compile(target: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(block for block in [result.stdout, result.stderr] if block)
    return result.returncode == 0, output


def run_ui_preflight(
    artifacts_dir: Path | None = None,
    runtime_output_dir: Path | None = None,
) -> dict[str, Any]:
    artifacts_dir = artifacts_dir or (ROOT / "artifacts")
    runtime_output_dir = runtime_output_dir or runtime_dir()
    runtime_output_dir.mkdir(parents=True, exist_ok=True)

    compile_payload = run_compile_check(targets=["tools"], artifacts_dir=artifacts_dir)
    compile_log_path = artifacts_dir / "compile_check.log"
    runtime_log_path = runtime_output_dir / "compileall_latest.log"
    if compile_log_path.exists():
        runtime_log_path.write_text(
            compile_log_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
        )

    ui_target = ROOT / "tools" / "ui_app.py"
    py_compile_ok, py_compile_output = _run_py_compile(ui_target)
    py_compile_log_path = artifacts_dir / "ui_py_compile.log"
    py_compile_log_path.write_text(py_compile_output, encoding="utf-8")

    conflict_hits = _scan_conflict_markers(_tracked_source_paths())

    status = "PASS"
    failures: list[str] = []
    if compile_payload.get("status") != "PASS":
        status = "FAIL"
        failures.append("compileall_failed")
    if not py_compile_ok:
        status = "FAIL"
        failures.append("ui_py_compile_failed")
    if conflict_hits:
        status = "FAIL"
        failures.append("conflict_markers_present")

    payload = {
        "schema_version": 1,
        "ts_utc": _iso_now(),
        "status": status,
        "failures": failures,
        "compile_check": compile_payload,
        "ui_py_compile": {
            "status": "PASS" if py_compile_ok else "FAIL",
            "log_path": to_repo_relative(py_compile_log_path),
        },
        "conflict_markers": conflict_hits,
    }
    _write_json(artifacts_dir / "ui_preflight_result.json", payload)
    return payload


def main() -> int:
    payload = run_ui_preflight()
    if payload.get("status") != "PASS":
        print("UI_PREFLIGHT_FAIL")
        for failure in payload.get("failures", []):
            print(f" - {failure}")
        return 1
    print("UI_PREFLIGHT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
