import json
import tempfile
import unittest
from pathlib import Path

from tools.migrate_event_archives import migrate_event_archives


def _write_event(path: Path) -> None:
    payload = {
        "schema_version": "v1",
        "ts_utc": "2024-01-01T00:00:00Z",
        "event_type": "test",
        "symbol": "TEST",
        "severity": "info",
        "message": "ok",
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


class MigrateEventArchivesTests(unittest.TestCase):
    def test_copy_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            logs_dir = root / "Logs"
            archive_dir = root / "Data"
            logs_dir.mkdir(parents=True, exist_ok=True)

            archive_file = logs_dir / "events_2024-01-02.jsonl"
            _write_event(archive_file)
            _write_event(logs_dir / "events.jsonl")

            result = migrate_event_archives(logs_dir, archive_dir, mode="copy")

            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["archives_found"], 1)
            self.assertTrue((archive_dir / archive_file.name).exists())
            self.assertTrue(archive_file.exists())


if __name__ == "__main__":
    unittest.main()
