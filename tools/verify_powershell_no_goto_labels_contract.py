from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "Logs",
    "Data",
}

GOTO_PATTERN = re.compile(r"^\s*goto\s+", re.IGNORECASE)
LABEL_PATTERN = re.compile(r"^\s*:\w+\s*$")

START_MARKER = "VERIFY_POWERSHELL_NO_GOTO_START"
SUMMARY_MARKER = "VERIFY_POWERSHELL_NO_GOTO_SUMMARY"
END_MARKER = "VERIFY_POWERSHELL_NO_GOTO_END"


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _iter_ps1_files(root: Path, scan_dirs: Iterable[Path]) -> Iterable[Path]:
    for directory in scan_dirs:
        if not directory.exists():
            continue
        for path in directory.rglob("*.ps1"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            yield path


def _scan_file(path: Path) -> list[dict[str, str | int]]:
    offenses: list[dict[str, str | int]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        if GOTO_PATTERN.search(raw_line):
            offenses.append(
                {
                    "file": path.as_posix(),
                    "line": line_number,
                    "rule": "goto_statement",
                    "content": raw_line.rstrip(),
                }
            )
        if LABEL_PATTERN.search(raw_line):
            offenses.append(
                {
                    "file": path.as_posix(),
                    "line": line_number,
                    "rule": "bare_label",
                    "content": raw_line.rstrip(),
                }
            )
    return offenses


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify PowerShell scripts avoid goto and bare label syntax."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root to scan for PowerShell scripts.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Artifacts directory to write results.",
    )
    return parser.parse_args(argv)


def _check_contract(root: Path) -> tuple[str, list[dict[str, str | int]]]:
    scan_dirs = [root / "scripts", root / "tools"]
    offenses: list[dict[str, str | int]] = []
    for script in sorted(_iter_ps1_files(root, scan_dirs)):
        offenses.extend(_scan_file(script))
    offenses.sort(key=lambda item: (str(item["file"]), int(item["line"])))
    status = "PASS" if not offenses else "FAIL"
    return status, offenses


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    status, offenses = _check_contract(args.root)

    artifacts_dir = args.artifacts_dir
    payload = {
        "status": status,
        "errors": offenses,
        "root": args.root.as_posix(),
        "ts_utc": _ts_utc(),
    }
    _write_json(
        artifacts_dir / "verify_powershell_no_goto_labels_contract.json", payload
    )

    report_lines = []
    for offense in offenses:
        report_lines.append(
            "{file}:{line}:{rule}: {content}".format(
                file=offense["file"],
                line=offense["line"],
                rule=offense["rule"],
                content=offense["content"],
            )
        )
    (artifacts_dir / "verify_powershell_no_goto_labels_contract.txt").write_text(
        "\n".join(report_lines) if report_lines else "ok",
        encoding="utf-8",
    )

    print(START_MARKER)
    for offense in offenses:
        print(
            "VERIFY_POWERSHELL_NO_GOTO_HIT"
            f"|file={offense['file']}|line={offense['line']}"
            f"|rule={offense['rule']}|content={offense['content']}"
        )
    print(f"{SUMMARY_MARKER}|status={status}|errors={len(offenses)}")
    print(END_MARKER)

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
