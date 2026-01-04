from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.paths import repo_root, to_repo_relative


ROOT = repo_root()
ARCHIVE_PATTERN = re.compile(r"events_\d{4}-\d{2}-\d{2}\.jsonl$")


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_archives(logs_dir: Path) -> list[Path]:
    if not logs_dir.exists():
        return []
    return sorted(
        [
            path
            for path in logs_dir.rglob("events_*.jsonl")
            if ARCHIVE_PATTERN.fullmatch(path.name)
        ]
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _relativize(paths: Iterable[Path]) -> list[str]:
    return [to_repo_relative(path) for path in paths]


def migrate_event_archives(
    logs_dir: Path,
    archive_dir: Path,
    *,
    mode: str = "copy",
) -> dict[str, Any]:
    archives = _find_archives(logs_dir)
    moved: list[Path] = []
    copied: list[Path] = []
    skipped_existing: list[Path] = []

    if not archives:
        return {
            "status": "NOOP",
            "mode": mode,
            "archives_found": 0,
            "moved": [],
            "copied": [],
            "skipped_existing": [],
        }

    archive_dir.mkdir(parents=True, exist_ok=True)
    for path in archives:
        destination = archive_dir / path.name
        if destination.exists():
            skipped_existing.append(destination)
            continue
        if mode == "move":
            shutil.move(str(path), str(destination))
            moved.append(destination)
        else:
            shutil.copy2(path, destination)
            copied.append(destination)

    status = "PASS"
    return {
        "status": status,
        "mode": mode,
        "archives_found": len(archives),
        "moved": _relativize(moved),
        "copied": _relativize(copied),
        "skipped_existing": _relativize(skipped_existing),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate archived events jsonl files.")
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=ROOT / "Logs",
        help="Logs directory to scan for archived events.",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=ROOT / "Data",
        help="Destination directory for archived events.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ROOT / "artifacts",
        help="Artifacts directory for migration report.",
    )
    parser.add_argument(
        "--mode",
        choices=["copy", "move"],
        default="copy",
        help="Copy or move archives into the archive directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    result = migrate_event_archives(args.logs_dir, args.archive_dir, mode=args.mode)
    result["ts_utc"] = _ts_utc()
    result["logs_dir"] = to_repo_relative(args.logs_dir)
    result["archive_dir"] = to_repo_relative(args.archive_dir)

    report_path = args.artifacts_dir / "migrate_event_archives.json"
    _write_json(report_path, result)

    print("MIGRATE_EVENT_ARCHIVES_START")
    print(
        "|".join(
            [
                "MIGRATE_EVENT_ARCHIVES_SUMMARY",
                f"status={result['status']}",
                f"mode={result['mode']}",
                f"archives_found={result['archives_found']}",
                f"moved={len(result['moved'])}",
                f"copied={len(result['copied'])}",
                f"skipped_existing={len(result['skipped_existing'])}",
                f"report={to_repo_relative(report_path)}",
            ]
        )
    )
    print("MIGRATE_EVENT_ARCHIVES_END")

    return 0 if result["status"] in {"PASS", "NOOP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
