from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _run_pwsh_parse(script_path: Path) -> tuple[int, str, str]:
    command = [
        "pwsh",
        "-NoProfile",
        "-Command",
        (
            "$errors = $null; $tokens = $null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{script_path.as_posix()}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "$result = @{"
            "status = if ($errors -and $errors.Count -gt 0) { 'FAIL' } else { 'PASS' }; "
            "errors = @($errors | ForEach-Object { $_.ToString() }); "
            f"script = '{script_path.as_posix()}'; "
            "ts_utc = (Get-Date -AsUTC).ToString('yyyy-MM-ddTHH:mm:ssZ'); "
            "}; "
            "$result | ConvertTo-Json -Depth 4"
        ),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = "\n".join(block for block in [result.stdout, result.stderr] if block)
    return result.returncode, result.stdout, combined


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PowerShell parse guard.")
    parser.add_argument(
        "--script",
        type=Path,
        default=Path("scripts/run_ui_windows.ps1"),
        help="Script to parse.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory to write results.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if shutil.which("pwsh") is None:
        payload = {
            "status": "ERROR",
            "reason": "pwsh_not_found",
            "script": args.script.as_posix(),
            "errors": [],
            "ts_utc": _ts_utc(),
        }
        _write_json(args.artifacts_dir / "ps_parse_result.json", payload)
        print("PS_PARSE_START")
        print("PS_PARSE_SUMMARY|status=ERROR|reason=pwsh_not_found")
        print("PS_PARSE_END")
        return 2

    rc, stdout, combined = _run_pwsh_parse(args.script)
    payload: dict[str, Any] = {
        "status": "ERROR",
        "reason": "parse_failed",
        "script": args.script.as_posix(),
        "errors": [],
        "raw_output": combined,
        "ts_utc": _ts_utc(),
    }
    try:
        parsed = json.loads(stdout) if stdout.strip() else {}
        if isinstance(parsed, dict):
            payload.update(parsed)
            payload["status"] = parsed.get("status", "ERROR")
            payload["reason"] = "ok" if payload["status"] == "PASS" else "parse_errors"
    except json.JSONDecodeError:
        payload["status"] = "ERROR"
        payload["reason"] = "invalid_json"
    payload["pwsh_returncode"] = rc

    _write_json(args.artifacts_dir / "ps_parse_result.json", payload)
    errors_count = len(payload.get("errors", []) or [])
    print("PS_PARSE_START")
    print(f"PS_PARSE_SUMMARY|status={payload['status']}|errors={errors_count}")
    print("PS_PARSE_END")

    if payload["status"] == "PASS":
        return 0
    return 1 if payload["status"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
