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

ADDITIONAL_CHILD_PATTERN = re.compile(r"(?i)-AdditionalChildPath\b")


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


def _split_top_level(line: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    in_single = False
    in_double = False
    for char in line:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if not in_single and not in_double:
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth = max(0, depth - 1)
            if char.isspace() and depth == 0:
                if current:
                    tokens.append("".join(current))
                    current = []
                continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _clean_token(token: str) -> str:
    cleaned = token.strip()
    cleaned = re.sub(r"^[\(\[{]+", "", cleaned)
    cleaned = re.sub(r"[\)\]}]+$", "", cleaned)
    return cleaned


def _positional_arg_count(tokens: list[str]) -> int:
    count = 0
    idx = 0
    while idx < len(tokens):
        token = _clean_token(tokens[idx])
        if not token:
            idx += 1
            continue
        if token.startswith("|") or token.startswith(";"):
            break
        if token in {")", "]", "}"}:
            idx += 1
            continue
        if token.startswith("-"):
            idx += 2
            continue
        count += 1
        idx += 1
    return count


def _has_join_path_positional_overflow(line: str) -> bool:
    tokens = _split_top_level(line)
    for idx, token in enumerate(tokens):
        cleaned = _clean_token(token)
        if cleaned.lower().startswith("join-path"):
            if cleaned.lower() == "join-path":
                count = _positional_arg_count(tokens[idx + 1 :])
                if count >= 3:
                    return True
            else:
                nested_tokens = _split_top_level(cleaned)
                if nested_tokens and _clean_token(nested_tokens[0]).lower() == "join-path":
                    count = _positional_arg_count(nested_tokens[1:])
                    if count >= 3:
                        return True
    return False


def _iter_ps1_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.ps1"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        yield path


def _scan_file(path: Path) -> list[dict[str, str | int]]:
    offenses: list[dict[str, str | int]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        line = _strip_inline_comment(raw_line)
        if "Join-Path" not in line and "-AdditionalChildPath" not in line:
            continue
        if ADDITIONAL_CHILD_PATTERN.search(line):
            offenses.append(
                {
                    "file": path.as_posix(),
                    "line": line_number,
                    "rule": "join_path_additional_child_path",
                    "content": raw_line.rstrip(),
                }
            )
        if "Join-Path" in line and _has_join_path_positional_overflow(line):
            offenses.append(
                {
                    "file": path.as_posix(),
                    "line": line_number,
                    "rule": "join_path_positional_args",
                    "content": raw_line.rstrip(),
                }
            )
    return offenses


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PowerShell Join-Path contract (PowerShell 5.1 compatibility)."
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
    _write_json(artifacts_dir / "powershell_join_path_contract_result.json", payload)

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
    (artifacts_dir / "powershell_join_path_contract.txt").write_text(
        "\n".join(report_lines) if report_lines else "ok",
        encoding="utf-8",
    )

    print("POWERSHELL_JOIN_PATH_CONTRACT_START")
    for offense in offenses:
        print(
            "POWERSHELL_JOIN_PATH_CONTRACT_HIT"
            f"|file={offense['file']}|line={offense['line']}"
            f"|rule={offense['rule']}|content={offense['content']}"
        )
    print(
        "POWERSHELL_JOIN_PATH_CONTRACT_SUMMARY"
        f"|status={status}|errors={len(offenses)}"
    )
    print("POWERSHELL_JOIN_PATH_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
