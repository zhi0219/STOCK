from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.supervisor import clear_kill_switch_files

SUMMARY_TAG = "KILL_SWITCH_RECOVERY_SUMMARY"


def _read_last_event(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def main() -> int:
    status = "PASS"
    issues: list[str] = []
    removed: list[str] = []
    events_path = ""

    print("KILL_SWITCH_RECOVERY_START")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        logs_dir = root / "Logs"
        data_dir = root / "Data"
        service_dir = logs_dir / "train_service"
        service_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        kill_service = service_dir / "KILL_SWITCH"
        kill_data = data_dir / "KILL_SWITCH"
        kill_service.write_text("STOP", encoding="utf-8")
        kill_data.write_text("STOP", encoding="utf-8")

        payload = clear_kill_switch_files([kill_service, kill_data], logs_dir)
        removed = [str(p) for p in payload.get("removed_files", [])]
        events_path = str(payload.get("events_path", ""))

        if kill_service.exists() or kill_data.exists():
            status = "FAIL"
            issues.append("kill_switch_files_not_removed")

        if not events_path:
            status = "FAIL"
            issues.append("events_path_missing")
        else:
            event = _read_last_event(Path(events_path))
            if not event:
                status = "FAIL"
                issues.append("event_missing")
            else:
                if event.get("event_type") != "KILL_SWITCH_CLEARED":
                    status = "FAIL"
                    issues.append("event_type_mismatch")
                removed_files = event.get("removed_files", [])
                if not isinstance(removed_files, list) or len(removed_files) < 2:
                    status = "FAIL"
                    issues.append("removed_files_missing")

    summary = "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"removed={','.join(removed) if removed else 'none'}",
            f"events_path={events_path or 'missing'}",
            f"issues={','.join(issues) if issues else 'none'}",
        ]
    )
    print(summary)
    print("KILL_SWITCH_RECOVERY_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
