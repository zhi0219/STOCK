from __future__ import annotations

import argparse
import io
import json
import sys
import tokenize
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PATTERNS = ["f\\\"", "rf\\\"", "fr\\\""]
EXTRA_CHECKS = ["unexpected_indent", "tokenize_error"]
DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
}


@dataclass(frozen=True)
class Hit:
    path: Path
    line: int
    column: int
    pattern: str
    line_text: str


def _iter_python_files(root: Path, excludes: set[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if any(part in excludes for part in path.parts):
            continue
        yield path


def _scan_file(path: Path) -> list[Hit]:
    hits: list[Hit] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in PATTERNS:
            start = 0
            while True:
                idx = line.find(pattern, start)
                if idx == -1:
                    break
                hits.append(
                    Hit(
                        path=path,
                        line=line_no,
                        column=idx + 1,
                        pattern=pattern,
                        line_text=line.rstrip("\n"),
                    )
                )
                start = idx + len(pattern)
    hits.extend(_scan_unexpected_indent(path, text))
    return hits


def _scan_unexpected_indent(path: Path, text: str) -> list[Hit]:
    hits: list[Hit] = []
    last_logical_line = ""
    indent_level = 0
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.NEWLINE:
                last_logical_line = token.line.rstrip()
            elif token.type == tokenize.INDENT:
                logical = last_logical_line.split("#", 1)[0].rstrip()
                stripped = logical.strip()
                if indent_level == 0 and stripped and not stripped.endswith(":"):
                    hits.append(
                        Hit(
                            path=path,
                            line=token.start[0],
                            column=token.start[1] + 1,
                            pattern="unexpected_indent",
                            line_text=token.line.rstrip("\n"),
                        )
                    )
                indent_level += 1
            elif token.type == tokenize.DEDENT:
                indent_level = max(indent_level - 1, 0)
    except (tokenize.TokenError, IndentationError) as exc:
        hits.append(
            Hit(
                path=path,
                line=1,
                column=1,
                pattern=f"tokenize_error:{type(exc).__name__}",
                line_text=str(exc),
            )
        )
    return hits


def _suggest_fix(pattern: str) -> str:
    if pattern.startswith("unexpected_indent"):
        return "align indentation or remove stray indent"
    if pattern.startswith("tokenize_error"):
        return "fix tokenize error (check indentation/syntax)"
    return pattern.replace("\\\"", '"')


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def run(root: Path, artifacts_dir: Path, excludes: set[str]) -> int:
    hits: list[Hit] = []
    files_scanned = 0

    for path in _iter_python_files(root, excludes):
        files_scanned += 1
        hits.extend(_scan_file(path))

    excerpt_path = artifacts_dir / "syntax_guard_excerpt.txt"
    result_path = artifacts_dir / "syntax_guard_result.json"

    if hits:
        lines = [
            "Syntax guard detected escaped f-string prefixes.",
            "Suggested fix: remove the backslash before the quote.",
            "",
        ]
        for hit in hits:
            rel_path = hit.path.relative_to(root)
            suggested = _suggest_fix(hit.pattern)
            lines.append(
                f"{rel_path}:{hit.line}:{hit.column} contains '{hit.pattern}' -> use '{suggested}'"
            )
            lines.append(f"  {hit.line_text}")
        _write_text(excerpt_path, "\n".join(lines))
    else:
        _write_text(excerpt_path, "No syntax guard hits detected.")

    payload = {
        "status": "PASS" if not hits else "FAIL",
        "hits": len(hits),
        "files_scanned": files_scanned,
        "patterns": PATTERNS + EXTRA_CHECKS,
        "excerpt_path": str(excerpt_path),
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": [
            {
                "file": str(hit.path.relative_to(root)),
                "line": hit.line,
                "column": hit.column,
                "pattern": hit.pattern,
                "line_text": hit.line_text,
            }
            for hit in hits
        ],
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print("SYNTAX_GUARD_START")
    print(
        "SYNTAX_GUARD_SUMMARY|"
        f"status={payload['status']}|"
        f"hits={payload['hits']}|"
        f"files_scanned={payload['files_scanned']}"
    )
    print("SYNTAX_GUARD_END")

    return 0 if not hits else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect escaped f-string prefixes.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repo root to scan.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Directory to write guard artifacts.",
    )
    args = parser.parse_args()

    try:
        return run(args.root, args.artifacts_dir, set(DEFAULT_EXCLUDES))
    except Exception as exc:  # pragma: no cover - fail closed
        artifacts_dir = args.artifacts_dir
        excerpt_path = artifacts_dir / "syntax_guard_excerpt.txt"
        result_path = artifacts_dir / "syntax_guard_result.json"
        _write_text(
            excerpt_path,
            "Syntax guard failed to run. See syntax_guard_result.json for details.",
        )
        payload = {
            "status": "ERROR",
            "hits": 0,
            "files_scanned": 0,
            "patterns": PATTERNS + EXTRA_CHECKS,
            "excerpt_path": str(excerpt_path),
            "error": f"{type(exc).__name__}: {exc}",
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print("SYNTAX_GUARD_START")
        print("SYNTAX_GUARD_SUMMARY|status=ERROR|hits=0|files_scanned=0")
        print("SYNTAX_GUARD_END")
        print(f"SYNTAX_GUARD_ERROR|{payload['error']}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
