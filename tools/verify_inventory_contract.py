from __future__ import annotations

import argparse
import json
import sys
import traceback
from difflib import unified_diff
from pathlib import Path

from tools import inventory_repo


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


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

    print("INVENTORY_CONTRACT_START")
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
        else:
            actual = _read_text(docs_path)
            if actual != expected:
                status_ok = False
                detail = "docs/inventory.md mismatch"
                diff = "\n".join(
                    unified_diff(
                        actual.splitlines(),
                        expected.splitlines(),
                        fromfile=str(docs_path),
                        tofile="generated/inventory.md",
                        lineterm="",
                    )
                )
                diff_output = "\n".join([diff, "", next_hint, ""])
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
        print("INVENTORY_CONTRACT_SUMMARY|status=PASS")
        return 0

    print(f"INVENTORY_CONTRACT_SUMMARY|status=FAIL|detail={detail}|{next_hint}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
