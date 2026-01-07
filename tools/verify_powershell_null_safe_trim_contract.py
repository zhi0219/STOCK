from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
RULE_ID = "trim_concat_requires_string_cast"

SCAN_DIRS = ("scripts", "tools")
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "Logs",
    "Data",
}

TRIM_PATTERN = re.compile(r"\.Trim\s*\(")


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    for idx, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:idx]
    return line


def _paren_pairs(line: str) -> dict[int, int]:
    stack: list[int] = []
    pairs: dict[int, int] = {}
    in_single = False
    in_double = False
    for idx, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if in_single or in_double:
            continue
        if char == "(":
            stack.append(idx)
        elif char == ")" and stack:
            pairs[idx] = stack.pop()
    return pairs


def _has_top_level_plus(expr: str) -> bool:
    depth = 0
    in_single = False
    in_double = False
    for char in expr:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if in_single or in_double:
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            depth = max(0, depth - 1)
            continue
        if char == "+" and depth == 0:
            return True
    return False


def _iter_ps1_files(root: Path) -> Iterable[Path]:
    for folder in SCAN_DIRS:
        base = root / folder
        if not base.exists():
            continue
        for path in base.rglob("*.ps1"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            yield path


def _line_number_from_index(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _scan_file(path: Path) -> list[dict[str, str | int]]:
    offenses: list[dict[str, str | int]] = []
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    stripped_lines = [_strip_inline_comment(line) for line in raw_lines]
    content = "\n".join(stripped_lines)
    if ".Trim" not in content:
        return offenses
    pairs = _paren_pairs(content)
    for match in TRIM_PATTERN.finditer(content):
        trim_start = match.start()
        idx = trim_start - 1
        while idx >= 0 and content[idx].isspace():
            idx -= 1
        if idx < 0 or content[idx] != ")":
            continue
        close_idx = idx
        open_idx = pairs.get(close_idx)
        if open_idx is None:
            continue
        expr = content[open_idx + 1 : close_idx]
        if not _has_top_level_plus(expr):
            continue
        line_number = _line_number_from_index(content, match.start())
        raw_line = raw_lines[line_number - 1] if line_number - 1 < len(raw_lines) else ""
        offenses.append(
            {
                "file": path.as_posix(),
                "line": line_number,
                "rule_id": RULE_ID,
                "content": raw_line.rstrip(),
            }
        )
    return offenses


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PowerShell null-safe Trim concatenation contract (PowerShell 5.1)."
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
    offenses: list[dict[str, str | int]] = []
    for script in sorted(_iter_ps1_files(root)):
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
    _write_json(artifacts_dir / "verify_powershell_null_safe_trim_contract.json", payload)

    report_lines = []
    for offense in offenses:
        report_lines.append(
            "{file}:{line}:{rule_id}: {content}".format(
                file=offense["file"],
                line=offense["line"],
                rule_id=offense["rule_id"],
                content=offense["content"],
            )
        )
    (artifacts_dir / "verify_powershell_null_safe_trim_contract.txt").write_text(
        "\n".join(report_lines) if report_lines else "ok",
        encoding="utf-8",
    )

    print("VERIFY_POWERSHELL_NULL_SAFE_TRIM_START")
    for offense in offenses:
        print(
            "VERIFY_POWERSHELL_NULL_SAFE_TRIM_HIT"
            f"|file={offense['file']}|line={offense['line']}"
            f"|rule_id={offense['rule_id']}|content={offense['content']}"
        )
    print(
        "VERIFY_POWERSHELL_NULL_SAFE_TRIM_SUMMARY"
        f"|status={status}|errors={len(offenses)}"
    )
    print("VERIFY_POWERSHELL_NULL_SAFE_TRIM_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
