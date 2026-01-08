from __future__ import annotations

import argparse
import json
from pathlib import Path

UTF8_BOM = b"\xef\xbb\xbf"


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_text(path: Path) -> tuple[str, bool]:
    data = path.read_bytes()
    has_bom = data.startswith(UTF8_BOM)
    text = data.decode("utf-8-sig")
    return text, has_bom


def _scan_backslashes(text: str) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "\\" in line:
            hits.append({"line": line_no, "content": line})
    return hits


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_normalize_newlines(text), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify inventory docs use POSIX paths.")
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    docs_path = repo_root / "docs" / "inventory.md"
    artifacts_dir = Path(args.artifacts_dir).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    json_report = artifacts_dir / "verify_inventory_docs_posix_paths.json"
    txt_report = artifacts_dir / "verify_inventory_docs_posix_paths.txt"

    print("INVENTORY_POSIX_PATHS_START")
    status_ok = True
    detail = "ok"
    next_hint = f"next=python -m tools.inventory_repo --artifacts-dir {artifacts_dir} --write-docs"
    hits: list[dict[str, object]] = []

    if not docs_path.exists():
        status_ok = False
        detail = "docs/inventory.md missing"
    else:
        text, has_bom = _read_text(docs_path)
        if has_bom:
            status_ok = False
            detail = "docs/inventory.md has UTF-8 BOM"
        else:
            normalized = _normalize_newlines(text)
            hits = _scan_backslashes(normalized)
            if hits:
                status_ok = False
                detail = "backslashes detected"

    result = {
        "status": "PASS" if status_ok else "FAIL",
        "detail": detail,
        "docs_path": "docs/inventory.md",
        "hits": hits,
        "next": None if status_ok else next_hint,
    }

    _write_text(json_report, json.dumps(result, indent=2, sort_keys=True))
    _write_text(txt_report, json.dumps(result, indent=2, sort_keys=True))

    if status_ok:
        print("INVENTORY_POSIX_PATHS_SUMMARY|status=PASS|hits=0")
        print("INVENTORY_POSIX_PATHS_END")
        return 0

    print(
        "INVENTORY_POSIX_PATHS_SUMMARY"
        f"|status=FAIL|detail={detail}|hits={len(hits)}|{next_hint}"
    )
    print("INVENTORY_POSIX_PATHS_END")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
