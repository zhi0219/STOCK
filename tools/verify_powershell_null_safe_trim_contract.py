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
STRING_CAST_PATTERN = re.compile(r"\[string\]\s*$", re.IGNORECASE)


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


def _variable_indices(expr: str) -> list[int]:
    indices: list[int] = []
    in_single = False
    in_double = False
    for idx, char in enumerate(expr):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if in_single or in_double:
            continue
        if char == "$":
            next_char = expr[idx + 1] if idx + 1 < len(expr) else ""
            if next_char.isalpha() or next_char in "_{(":
                indices.append(idx)
    return indices


def _all_variables_cast_to_string(expr: str) -> bool:
    indices = _variable_indices(expr)
    if not indices:
        return True
    for idx in indices:
        prefix = expr[:idx]
        if not STRING_CAST_PATTERN.search(prefix):
            return False
    return True


def _iter_ps1_files(root: Path) -> Iterable[Path]:
    for folder in SCAN_DIRS:
        base = root / folder
        if not base.exists():
            continue
        for path in base.rglob("*.ps1"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            yield path


def _scan_file(path: Path) -> list[dict[str, str | int]]:
    offenses: list[dict[str, str | int]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        line = _strip_inline_comment(raw_line)
        if ".Trim" not in line:
            continue
        pairs = _paren_pairs(line)
        for match in TRIM_PATTERN.finditer(line):
            trim_start = match.start()
            close_idx = None
            for idx in sorted(pairs.keys(), reverse=True):
                if idx < trim_start and not line[idx + 1 : trim_start].strip():
                    close_idx = idx
                    break
            if close_idx is None:
                continue
            open_idx = pairs[close_idx]
            expr = line[open_idx + 1 : close_idx]
            if not _has_top_level_plus(expr):
                continue
            if not _variable_indices(expr):
                continue
            if _all_variables_cast_to_string(expr):
                continue
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
