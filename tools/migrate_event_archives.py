from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.paths import repo_root, to_repo_relative


ROOT = repo_root()
ARCHIVE_PATTERN = re.compile(r"events_\d{4}-\d{2}-\d{2}\.jsonl$")
MAX_SKIPPED = 50


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _find_archives(logs_dir: Path, archive_dir: Path) -> list[Path]:
    if not logs_dir.exists():
        return []
    archives = []
    for path in logs_dir.rglob("events_*.jsonl"):
        if not ARCHIVE_PATTERN.fullmatch(path.name):
            continue
        if _is_within(path, archive_dir):
            continue
        archives.append(path)
    return sorted(archives)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def migrate_event_archives(
    logs_dir: Path,
    archive_dir: Path,
    *,
    mode: str = "move",
) -> dict[str, Any]:
    archives = _find_archives(logs_dir, archive_dir)
    moved: list[Path] = []
    copied: list[Path] = []
    skipped: list[dict[str, str]] = []

    archive_dir.mkdir(parents=True, exist_ok=True)
    for path in archives:
        destination = archive_dir / path.name
        if destination.exists():
            continue
        try:
            if mode == "move":
                shutil.move(str(path), str(destination))
                moved.append(destination)
            else:
                shutil.copy2(path, destination)
                copied.append(destination)
        except (PermissionError, OSError) as exc:
            skipped.append({"path": to_repo_relative(path), "error": repr(exc)})
            continue

    status = "PASS" if not skipped else "FAIL"
    limited_skipped = skipped[:MAX_SKIPPED]
    return {
        "status": status,
        "mode": mode,
        "archives_found": len(archives),
        "moved_count": len(moved),
        "copied_count": len(copied),
        "skipped_count": len(skipped),
        "skipped_paths": [entry["path"] for entry in limited_skipped],
        "skipped": limited_skipped,
        "skipped_truncated": len(skipped) > len(limited_skipped),
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
        default=ROOT / "Logs" / "event_archives",
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
        default="move",
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
    text_path = args.artifacts_dir / "migrate_event_archives.txt"
    _write_json(report_path, result)

    summary_line = "|".join(
        [
            "MIGRATE_EVENT_ARCHIVES_SUMMARY",
            f"status={result['status']}",
            f"mode={result['mode']}",
            f"archives_found={result['archives_found']}",
            f"moved={result['moved_count']}",
            f"copied={result['copied_count']}",
            f"skipped={result['skipped_count']}",
            f"report={to_repo_relative(report_path)}",
        ]
    )

    markers = ["MIGRATE_EVENT_ARCHIVES_START", summary_line]
    if result["status"] == "FAIL":
        markers.append(
            "MIGRATE_EVENT_ARCHIVES_NEXT="
            "close locked files and rerun python -m tools.migrate_event_archives"
        )
    markers.append("MIGRATE_EVENT_ARCHIVES_END")
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("\n".join(markers) + "\n", encoding="utf-8")

    print("\n".join(markers))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
