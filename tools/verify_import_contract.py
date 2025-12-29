from __future__ import annotations

import argparse
import importlib
import json
import traceback
from pathlib import Path
from typing import Any

IMPORT_START = "IMPORT_CONTRACT_START"
IMPORT_END = "IMPORT_CONTRACT_END"
IMPORT_SUMMARY = "IMPORT_CONTRACT_SUMMARY"
IMPORT_TRACEBACK = "IMPORT_CONTRACT_TRACEBACK"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import-contract verifier (CI-safe).")
    parser.add_argument(
        "--module",
        default="tools.verify_pr23_gate",
        help="Module path to import (e.g., tools.verify_pr23_gate)",
    )
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Artifacts directory for result/traceback outputs",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    module = args.module
    artifacts_dir = Path(args.artifacts_dir)
    result_path = artifacts_dir / "import_contract_result.json"
    traceback_path = artifacts_dir / "import_contract_traceback.txt"

    print(IMPORT_START)

    status = "PASS"
    exception_type = None
    exception_message = None
    traceback_text = ""
    traceback_file = ""

    try:
        importlib.import_module(module)
    except Exception as exc:
        status = "FAIL"
        exception_type = type(exc).__name__
        exception_message = str(exc)
        traceback_text = traceback.format_exc()
        print(traceback_text)
        traceback_path.parent.mkdir(parents=True, exist_ok=True)
        traceback_path.write_text(traceback_text, encoding="utf-8")
        traceback_file = str(traceback_path)

    summary_line = " ".join(
        [
            IMPORT_SUMMARY,
            f"status={status}",
            f"module={module}",
            f"exc={exception_type or 'none'}",
        ]
    )
    print(summary_line)
    print(f"{IMPORT_TRACEBACK} path={traceback_file or 'n/a'}")
    print(IMPORT_END)

    result = {
        "status": status,
        "module": module,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "traceback_file": traceback_file or None,
    }
    _write_json(result_path, result)

    if status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
