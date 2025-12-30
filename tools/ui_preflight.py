from __future__ import annotations

import json
import os
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


def _tracked_source_paths() -> tuple[list[Path], str | None]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return [], "git_ls_files_failed"
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        path = ROOT / line.strip()
        if path.suffix.lower() in {".py", ".sh", ".md", ".ps1"}:
            paths.append(path)
    return paths, None


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


def _excerpt(text: str, limit: int = 320) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def run_ui_preflight(
    artifacts_dir: Path | None = None,
    runtime_output_dir: Path | None = None,
) -> dict[str, Any]:
    artifacts_dir = artifacts_dir or (ROOT / "artifacts")
    runtime_output_dir = runtime_output_dir or runtime_dir()
    runtime_output_dir.mkdir(parents=True, exist_ok=True)

    result_path = artifacts_dir / "ui_preflight_result.json"
    payload: dict[str, Any] = {
        "schema_version": 2,
        "ts_utc": _iso_now(),
        "status": "PASS",
        "reason_code": "",
        "reason_detail": "",
        "failing_step": "",
        "traceback_excerpt": "",
        "suggested_fix": "",
        "compile_check": {},
        "ui_py_compile": {},
        "conflict_markers": [],
        "skipped_steps": [],
        "runtime_paths": {
            "repo_root": to_repo_relative(ROOT),
            "runtime_dir": to_repo_relative(runtime_dir()),
        },
    }

    try:
        if str(os.environ.get("PR40_FORCE_FAIL", "")) == "1":
            payload["status"] = "FAIL"
            payload["reason_code"] = "forced_fail"
            payload["reason_detail"] = "PR40_FORCE_FAIL=1 requested forced failure."
            payload["failing_step"] = "forced_fail"
            payload["suggested_fix"] = "Unset PR40_FORCE_FAIL to allow preflight to pass."
            _write_json(result_path, payload)
            return payload

        compile_payload = run_compile_check(targets=["tools"], artifacts_dir=artifacts_dir)
        payload["compile_check"] = compile_payload
        compile_log_path = artifacts_dir / "compile_check.log"
        runtime_log_path = runtime_output_dir / "compileall_latest.log"
        if compile_log_path.exists():
            runtime_log_path.write_text(
                compile_log_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
            )

        ui_target = ROOT / "tools" / "ui_app.py"
        if not ui_target.exists():
            payload["status"] = "FAIL"
            payload["reason_code"] = "ui_entrypoint_missing"
            payload["reason_detail"] = f"UI entrypoint missing: {to_repo_relative(ui_target)}"
            payload["failing_step"] = "entrypoint_check"
            payload["suggested_fix"] = "Restore tools/ui_app.py or check repo integrity."
            _write_json(result_path, payload)
            return payload

        py_compile_ok, py_compile_output = _run_py_compile(ui_target)
        py_compile_log_path = artifacts_dir / "ui_py_compile.log"
        py_compile_log_path.write_text(py_compile_output, encoding="utf-8")
        payload["ui_py_compile"] = {
            "status": "PASS" if py_compile_ok else "FAIL",
            "log_path": to_repo_relative(py_compile_log_path),
        }

        conflict_hits: list[dict[str, Any]] = []
        tracked_paths, git_error = _tracked_source_paths()
        if git_error:
            payload["skipped_steps"].append(
                {"step": "conflict_scan", "reason": git_error}
            )
        else:
            conflict_hits = _scan_conflict_markers(tracked_paths)
        payload["conflict_markers"] = conflict_hits

        failures: list[str] = []
        if compile_payload.get("status") != "PASS":
            failures.append("compileall_failed")
            payload["reason_code"] = "compileall_failed"
            payload["reason_detail"] = "compileall failed for tools/."
            payload["failing_step"] = "compileall"
            payload["suggested_fix"] = "Review artifacts/compile_check.log for syntax errors."
        if not py_compile_ok:
            failures.append("ui_py_compile_failed")
            payload["reason_code"] = "ui_py_compile_failed"
            payload["reason_detail"] = "py_compile failed for tools/ui_app.py."
            payload["failing_step"] = "py_compile"
            payload["suggested_fix"] = "Fix syntax/indentation in tools/ui_app.py."
        if conflict_hits:
            failures.append("conflict_markers_present")
            payload["reason_code"] = "conflict_markers_present"
            payload["reason_detail"] = "Conflict markers detected in tracked sources."
            payload["failing_step"] = "conflict_scan"
            payload["suggested_fix"] = "Resolve conflict markers (<<<<<<<, =======, >>>>>>>)."

        if failures:
            payload["status"] = "FAIL"
            if not payload["reason_detail"]:
                payload["reason_detail"] = "UI preflight failed."
        else:
            payload["reason_code"] = "ok"
            payload["reason_detail"] = "UI preflight passed."
    except Exception as exc:
        payload["status"] = "FAIL"
        payload["reason_code"] = "preflight_exception"
        payload["reason_detail"] = f"{type(exc).__name__}: {exc}"
        payload["failing_step"] = "exception"
        payload["traceback_excerpt"] = _excerpt(payload["reason_detail"])
        payload["suggested_fix"] = "Inspect ui_preflight_result.json for details."

    payload["reason_detail"] = _excerpt(str(payload.get("reason_detail", "")), 1000)
    _write_json(result_path, payload)
    return payload


def main() -> int:
    payload = run_ui_preflight()
    if payload.get("status") != "PASS":
        detail = payload.get("reason_detail", "")
        print("UI_PREFLIGHT_FAIL")
        if detail:
            print(f"UI_PREFLIGHT_REASON|{detail}")
        return 1
    print("UI_PREFLIGHT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
