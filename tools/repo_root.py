from __future__ import annotations

import argparse
from pathlib import Path


def _find_repo_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return None


def resolve_repo_root() -> Path | None:
    candidates = [Path.cwd(), Path(__file__).resolve()]
    for start in candidates:
        root = _find_repo_root(start)
        if root:
            return root
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Locate repo root.")
    parser.add_argument(
        "--print",
        dest="print_path",
        action="store_true",
        help="Print repo root to stdout.",
    )
    args = parser.parse_args()

    root = resolve_repo_root()
    if root is None:
        return 1
    if args.print_path:
        print(root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
