from __future__ import annotations

import argparse
import json
import sys
import traceback
from difflib import unified_diff
from pathlib import Path

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
    path.write_text(_normalize_newlines(text), encoding="utf-8")


def _read_text(path: Path) -> tuple[str, bool]:
    data = path.read_bytes()
    has_bom = data.startswith(UTF8_BOM)
    text = data.decode("utf-8-sig")
    return text, has_bom


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
    json_report = artifacts_dir / "verify_inventory_contract.json"
    txt_report = artifacts_dir / "verify_inventory_contract.txt"

    print("VERIFY_INVENTORY_CONTRACT_START")
    status_ok = True
    detail = "ok"
    next_hint = f"next=python -m tools.inventory_repo --artifacts-dir {artifacts_dir} --write-docs"

    try:
        inventory = inventory_repo.generate_inventory(repo_root)
        expected = inventory_repo._render_markdown(inventory)
        docs_path = repo_root / "docs" / "inventory.md"

        if not docs_path.exists():
            status_ok = False
            detail = "docs/inventory.md missing"
            diff_output = "\n".join([detail, "", next_hint, ""])
            _write_text(diff_path, diff_output)
        else:
            actual, has_bom = _read_text(docs_path)
            if has_bom:
                status_ok = False
                detail = "docs/inventory.md has UTF-8 BOM"
            elif not _normalized_equal(actual, expected):
                status_ok = False
                detail = "docs/inventory.md mismatch"

            if not status_ok:
                normalized_actual = _normalize_newlines(actual)
                normalized_expected = _normalize_newlines(expected)
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
    except Exception as exc:  # pragma: no cover - defensive
        status_ok = False
        detail = f"error={exc}"
        _write_text(diff_path, "\n".join([traceback.format_exc(), next_hint, ""]))

    result = {
        "status": "PASS" if status_ok else "FAIL",
        "detail": detail,
        "docs_path": "docs/inventory.md",
        "diff_path": str(diff_path),
        "next": None if status_ok else next_hint,
    }

    _write_text(json_report, json.dumps(result, indent=2, sort_keys=True))
    _write_text(txt_report, json.dumps(result, indent=2, sort_keys=True))

    if status_ok:
        print("VERIFY_INVENTORY_CONTRACT_SUMMARY|status=PASS")
        print("VERIFY_INVENTORY_CONTRACT_END")
        return 0

    print(f"VERIFY_INVENTORY_CONTRACT_SUMMARY|status=FAIL|detail={detail}|{next_hint}")
    print("VERIFY_INVENTORY_CONTRACT_END")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
