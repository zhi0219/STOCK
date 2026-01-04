import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import repo_hygiene
from tools.paths import repo_root


class RepoHygieneCleanupTests(unittest.TestCase):
    def test_cleanup_skips_locked_file(self) -> None:
        root = repo_root()
        logs_dir = root / "Logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        target = logs_dir / "runtime_lock_test.json"
        target.write_text("lock", encoding="utf-8")

        rel_target = repo_hygiene.normalize_path(str(target.relative_to(root)))
        original_unlink = Path.unlink

        def _side_effect(self: Path) -> None:
            if self == target:
                raise PermissionError("locked")
            return original_unlink(self)

        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts_dir = Path(tmp_dir)
            try:
                with mock.patch.object(Path, "unlink", _side_effect):
                    report = repo_hygiene.remove_runtime_paths(
                        [rel_target],
                        repo_hygiene.safe_delete_roots(),
                        artifacts_dir=artifacts_dir,
                    )
            finally:
                if target.exists():
                    original_unlink(target)

            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["skipped_count"], 1)
            self.assertIn(rel_target, report["requested_paths"])
            self.assertTrue((artifacts_dir / repo_hygiene.CLEANUP_REPORT_NAME).exists())
            self.assertTrue((artifacts_dir / repo_hygiene.CLEANUP_TEXT_NAME).exists())


if __name__ == "__main__":
    unittest.main()
