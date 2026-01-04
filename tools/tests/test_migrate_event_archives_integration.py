import json
import tempfile
import unittest
from pathlib import Path

from tools import migrate_event_archives
from tools import verify_repo_hygiene
from tools.paths import repo_root


class MigrateEventArchivesIntegrationTests(unittest.TestCase):
    def _write_event(self, path: Path) -> None:
        payload = {
            "schema_version": 1,
            "ts_utc": "2099-01-01T00:00:00Z",
            "event_type": "test",
            "symbol": "TEST",
            "severity": "info",
            "message": "ok",
        }
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def test_copy_migration_and_hygiene(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            logs_dir = Path(temp_root) / "Logs"
            legacy_dir = logs_dir / "_event_archives"
            archive_dir = logs_dir / "event_archives"
            legacy_dir.mkdir(parents=True, exist_ok=True)
            archive_dir.mkdir(parents=True, exist_ok=True)
            legacy_path = legacy_dir / "events_2099-01-01.jsonl"
            self._write_event(legacy_path)

            with tempfile.TemporaryDirectory() as tmp_dir:
                result = migrate_event_archives.main(
                    [
                        "--logs-dir",
                        str(logs_dir),
                        "--archive-dir",
                        str(archive_dir),
                        "--artifacts-dir",
                        tmp_dir,
                        "--mode",
                        "copy",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertTrue(legacy_path.exists())
            self.assertTrue((archive_dir / legacy_path.name).exists())

        repo_root_path = repo_root()
        repo_logs = repo_root_path / "Logs"
        repo_legacy = repo_logs / "_event_archives"
        repo_created_dir = not repo_legacy.exists()
        repo_legacy.mkdir(parents=True, exist_ok=True)
        repo_path = repo_legacy / "events_2099-02-01.jsonl"
        self._write_event(repo_path)
        try:
            self.assertEqual(verify_repo_hygiene.main(), 0)
        finally:
            if repo_path.exists():
                repo_path.unlink()
            if repo_created_dir and repo_legacy.exists() and not any(repo_legacy.iterdir()):
                repo_legacy.rmdir()

    def test_move_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            logs_dir = Path(temp_root) / "Logs"
            legacy_dir = logs_dir / "_event_archives"
            archive_dir = logs_dir / "event_archives"
            legacy_dir.mkdir(parents=True, exist_ok=True)
            archive_dir.mkdir(parents=True, exist_ok=True)
            legacy_path = legacy_dir / "events_2099-01-02.jsonl"
            self._write_event(legacy_path)

            with tempfile.TemporaryDirectory() as tmp_dir:
                result = migrate_event_archives.main(
                    [
                        "--logs-dir",
                        str(logs_dir),
                        "--archive-dir",
                        str(archive_dir),
                        "--artifacts-dir",
                        tmp_dir,
                        "--mode",
                        "move",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertFalse(legacy_path.exists())
            moved_path = archive_dir / legacy_path.name
            self.assertTrue(moved_path.exists())


if __name__ == "__main__":
    unittest.main()
