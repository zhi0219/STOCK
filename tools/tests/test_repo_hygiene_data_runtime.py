import unittest
from pathlib import Path

from tools import repo_hygiene
from tools.paths import repo_root


class RepoHygieneDataRuntimeTests(unittest.TestCase):
    def test_logs_data_runtime_ignored(self) -> None:
        root = repo_root()
        runtime_dir = root / "Logs" / "data_runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        dummy_path = runtime_dir / "dummy_runtime.txt"
        try:
            dummy_path.write_text("runtime", encoding="utf-8")
            status_lines, error = repo_hygiene.git_status_porcelain(include_ignored=True)
            self.assertIsNone(error)
            ignored_paths = [repo_hygiene.normalize_path(line[3:]) for line in status_lines if line.startswith("!! ")]
            normalized_dummy = repo_hygiene.normalize_path(str(dummy_path.relative_to(root)))
            self.assertTrue(
                any(path.startswith(repo_hygiene.normalize_path("Logs/data_runtime/")) for path in ignored_paths)
                or normalized_dummy in ignored_paths
            )
            summary = repo_hygiene.scan_repo()
            ignored_entries = summary.get("ignored", [])
            ignored_paths = [
                repo_hygiene.normalize_path(entry.get("path", ""))
                for entry in ignored_entries
                if isinstance(entry, dict)
            ]
            self.assertTrue(
                any(path.startswith(repo_hygiene.normalize_path("Logs/data_runtime/")) for path in ignored_paths)
            )
            untracked_paths = [
                repo_hygiene.normalize_path(entry.get("path", ""))
                for entry in summary.get("untracked", [])
                if isinstance(entry, dict)
            ]
            self.assertFalse(
                any(path.startswith(repo_hygiene.normalize_path("Logs/data_runtime/")) for path in untracked_paths)
            )
            if not summary.get("tracked_modified") and not summary.get("untracked"):
                self.assertEqual(summary.get("status"), "PASS")
        finally:
            if dummy_path.exists():
                dummy_path.unlink()


if __name__ == "__main__":
    unittest.main()
