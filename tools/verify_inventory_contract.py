from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import traceback
from difflib import unified_diff
from pathlib import Path
from datetime import datetime, timezone

from tools import inventory_repo

UTF8_BOM = b"\xef\xbb\xbf"


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _truncate(value: str | None, limit: int = 160) -> str:
    if value is None:
        return "<none>"
    sanitized = value.replace("\t", "\\t")
    if len(sanitized) <= limit:
        return sanitized
    return f"{sanitized[:limit - 3]}..."


def _classify_mismatch(actual_line: str | None, expected_line: str | None) -> str:
    if (actual_line and "\ufeff" in actual_line) or (expected_line and "\ufeff" in expected_line):
        return "encoding"
    if (actual_line and "\\" in actual_line) or (expected_line and "\\" in expected_line):
        return "path_separator"
    return "other"


def _context_snippet(lines: list[str], index: int, radius: int = 2) -> list[dict[str, object]]:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return [{"line": i + 1, "text": lines[i]} for i in range(start, end)]


def _diff_summary(actual: str, expected: str, limit: int = 10) -> dict[str, object]:
    actual_lines = actual.splitlines()
    expected_lines = expected.splitlines()
    max_len = max(len(actual_lines), len(expected_lines))
    mismatches: list[dict[str, object]] = []
    for index in range(max_len):
        actual_line = actual_lines[index] if index < len(actual_lines) else None
        expected_line = expected_lines[index] if index < len(expected_lines) else None
        if actual_line != expected_line:
            mismatches.append(
                {
                    "line": index + 1,
                    "actual": _truncate(actual_line),
                    "expected": _truncate(expected_line),
                    "context": {
                        "actual": _context_snippet(actual_lines, index),
                        "expected": _context_snippet(expected_lines, index),
                    },
                    "classification": _classify_mismatch(actual_line, expected_line),
                }
            )
        if len(mismatches) >= limit:
            break
    return {
        "limit": limit,
        "actual_total_lines": len(actual_lines),
        "expected_total_lines": len(expected_lines),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def _first_diff_summary(actual: str, expected: str) -> str:
    actual_lines = actual.splitlines()
    expected_lines = expected.splitlines()
    max_len = max(len(actual_lines), len(expected_lines))
    for index in range(max_len):
        actual_line = actual_lines[index] if index < len(actual_lines) else None
        expected_line = expected_lines[index] if index < len(expected_lines) else None
        if actual_line != expected_line:
            return (
                "FIRST_DIFF|line="
                f"{index + 1}|actual={_truncate(actual_line)}|expected={_truncate(expected_line)}"
            )
    return "FIRST_DIFF|none"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_normalize_newlines(text), encoding="utf-8", newline="\n")


def _count_crlf_pairs(data: bytes) -> int:
    return data.count(b"\r\n")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clip_text(value: str | None, limit: int = 2000) -> str:
    if value is None:
        return ""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _git_check_attr(repo_root: Path, path: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            ["git", "check-attr", "-a", "--", str(path)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "ok": True,
            "stdout": _clip_text(completed.stdout),
            "stderr": _clip_text(completed.stderr),
        }
    except Exception as exc:  # pragma: no cover - best effort
        return {
            "ok": False,
            "stdout": "",
            "stderr": _clip_text(str(exc)),
        }


def _normalized_equal(actual: str, expected: str) -> bool:
    return _normalize_newlines(actual) == _normalize_newlines(expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify inventory docs match generator output.")
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    artifacts_dir = Path(args.artifacts_dir).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    diff_path = artifacts_dir / "inventory_diff.txt"
    diff_summary_path = artifacts_dir / "inventory_diff_summary.json"
    json_report = artifacts_dir / "verify_inventory_contract.json"
    txt_report = artifacts_dir / "verify_inventory_contract.txt"
    eol_stats_path = artifacts_dir / "verify_inventory_eol_stats.json"

    print("VERIFY_INVENTORY_CONTRACT_START")
    status_ok = True
    detail = "ok"
    next_hint = f"next=python -m tools.inventory_repo --artifacts-dir {artifacts_dir} --write-docs"

    expected = ""
    actual = ""
    docs_data = b""
    gen_data = b""
    docs_path = repo_root / "docs" / "inventory.md"
    gen_markdown_path = artifacts_dir / "repo_inventory.md"
    gen_path = "artifacts/repo_inventory.md"

    try:
        inventory = inventory_repo.generate_inventory(repo_root)
        expected = inventory_repo._render_markdown(inventory)
        if gen_markdown_path.exists():
            gen_data = gen_markdown_path.read_bytes()
        else:
            gen_data = expected.encode("utf-8")
            gen_path = "generated/inventory.md"

        if not docs_path.exists():
            status_ok = False
            detail = "docs/inventory.md missing"
            actual = ""
        else:
            docs_data = docs_path.read_bytes()
            actual = docs_data.decode("utf-8-sig")
            has_bom = docs_data.startswith(UTF8_BOM)
            if has_bom:
                status_ok = False
                detail = "docs/inventory.md has UTF-8 BOM"
            elif _count_crlf_pairs(docs_data) > 0 or b"\r" in docs_data:
                status_ok = False
                detail = "docs/inventory.md contains CRLF"
            elif not _normalized_equal(actual, expected):
                status_ok = False
                detail = "docs/inventory.md mismatch"

        if not status_ok:
            normalized_actual = _normalize_newlines(actual)
            normalized_expected = _normalize_newlines(expected)
            diff_summary = _diff_summary(normalized_actual, normalized_expected)
            diff = "\n".join(
                unified_diff(
                    normalized_actual.splitlines(),
                    normalized_expected.splitlines(),
                    fromfile=str(docs_path),
                    tofile="generated/inventory.md",
                    lineterm="",
                )
            )
            first_diff = _first_diff_summary(normalized_actual, normalized_expected)
            diff_output = "\n".join([first_diff, detail, diff, "", next_hint, ""])
            _write_text(diff_path, diff_output)
            _write_text(diff_summary_path, json.dumps(diff_summary, indent=2, sort_keys=True))
    except Exception as exc:  # pragma: no cover - defensive
        status_ok = False
        detail = f"error={exc}"
        _write_text(diff_path, "\n".join([traceback.format_exc(), next_hint, ""]))

    eol_stats = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "docs_path": "docs/inventory.md",
        "docs_len": len(docs_data),
        "docs_crlf_pairs": _count_crlf_pairs(docs_data),
        "docs_has_bom": docs_data.startswith(UTF8_BOM),
        "docs_sha256": _sha256(docs_data),
        "gen_path": gen_path,
        "gen_len": len(gen_data),
        "gen_crlf_pairs": _count_crlf_pairs(gen_data),
        "gen_has_bom": gen_data.startswith(UTF8_BOM),
        "gen_sha256": _sha256(gen_data),
        "git_check_attr": _git_check_attr(repo_root, docs_path),
        "verdict": "PASS" if status_ok else "FAIL",
        "detail": detail,
    }
    _write_text(eol_stats_path, json.dumps(eol_stats, indent=2, sort_keys=True))

    result = {
        "status": "PASS" if status_ok else "FAIL",
        "detail": detail,
        "docs_path": "docs/inventory.md",
        "diff_path": str(diff_path),
        "diff_summary_path": str(diff_summary_path),
        "next": None if status_ok else next_hint,
    }

    _write_text(json_report, json.dumps(result, indent=2, sort_keys=True))
    _write_text(txt_report, json.dumps(result, indent=2, sort_keys=True))

    if status_ok:
        print("VERIFY_INVENTORY_CONTRACT_SUMMARY|status=PASS")
        print("VERIFY_INVENTORY_CONTRACT_END")
        return 0

    print(
        "VERIFY_INVENTORY_EOLS"
        f"|docs_crlf_pairs={eol_stats['docs_crlf_pairs']}"
        f"|docs_has_bom={eol_stats['docs_has_bom']}"
        f"|docs_len={eol_stats['docs_len']}"
        f"|gen_crlf_pairs={eol_stats['gen_crlf_pairs']}"
        f"|gen_has_bom={eol_stats['gen_has_bom']}"
        f"|gen_len={eol_stats['gen_len']}"
        f"|stats={eol_stats_path.as_posix()}"
    )
    print(f"VERIFY_INVENTORY_CONTRACT_SUMMARY|status=FAIL|detail={detail}|{next_hint}")
    print("VERIFY_INVENTORY_CONTRACT_END")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
