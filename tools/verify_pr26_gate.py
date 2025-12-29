from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts" / "pr26_gate"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fs_atomic import atomic_write_json


class GateError(Exception):
    pass


@dataclass
class Evidence:
    events_path: Path
    evidence_dir: Path


def _event_path(now: datetime) -> Path:
    return ROOT / "Logs" / f"events_{now:%Y-%m-%d}.jsonl"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_atomic_write() -> None:
    tmp_dir = ARTIFACTS_DIR / "atomic_write"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_dir / "state.json"
    payload = {"schema_version": 1, "ts_utc": datetime.now(timezone.utc).isoformat(), "status": "ok"}
    atomic_write_json(target, payload)
    data = _load_json(target)
    if data.get("schema_version") != 1 or data.get("status") != "ok":
        raise GateError("atomic_write_json did not persist expected payload")


def _run_action_center_apply() -> Evidence:
    evidence_dir = ARTIFACTS_DIR / "action_center_apply"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    action_id = "ACTION_REBUILD_PROGRESS_INDEX"
    confirm = f"APPLY:{action_id}"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "action_center_apply.py"),
        "--action-id",
        action_id,
        "--confirm",
        confirm,
        "--dry-run",
        "--evidence-dir",
        str(evidence_dir),
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise GateError(f"action_center_apply dry-run failed: {result.stderr or result.stdout}")

    summary_path = evidence_dir / "action_center_apply_summary.json"
    log_path = evidence_dir / "action_center_apply.log"
    if not summary_path.exists() or not log_path.exists():
        raise GateError("action_center_apply evidence files missing")

    summary = _load_json(summary_path)
    if summary.get("status") != "DRY_RUN":
        raise GateError("action_center_apply summary did not record dry-run status")

    events_path = _event_path(datetime.now(timezone.utc))
    if not events_path.exists():
        raise GateError("events jsonl missing after action_center_apply")
    return Evidence(events_path=events_path, evidence_dir=evidence_dir)


def _check_apply_events(events_path: Path) -> None:
    lines = events_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    event_types = {event.get("event_type") for event in events}
    missing = {
        "ACTION_CENTER_APPLY_ATTEMPT",
        "ACTION_CENTER_APPLY_DRY_RUN",
    }.difference(event_types)
    if missing:
        raise GateError(f"missing action_center_apply events: {sorted(missing)}")


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PR26 gate: validate atomic write + Action Center apply.")
    parser.add_argument("--force-fail", action="store_true", help="Force a failure for evidence-pack demo.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _check_atomic_write()
        evidence = _run_action_center_apply()
        _check_apply_events(evidence.events_path)
        if args.force_fail or os.environ.get("PR26_FORCE_FAIL") == "1":
            raise GateError("PR26 forced failure (demo flag set).")
    except GateError as exc:
        print(f"PR26 gate failed: {exc}")
        return 1
    print("PR26 gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
