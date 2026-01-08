import json
import tempfile
import unittest
from pathlib import Path

from tools import verify_inventory_docs_posix_paths


class VerifyInventoryDocsPosixPathsTests(unittest.TestCase):
    def test_passes_with_posix_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            docs_dir = repo_root / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / "inventory.md").write_text(
                "# Inventory\n- `tools/verify_x.py`\n",
                encoding="utf-8",
            )
            artifacts_dir = repo_root / "artifacts"
            rc = verify_inventory_docs_posix_paths.main(
                ["--artifacts-dir", str(artifacts_dir), "--repo-root", str(repo_root)]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(
                (artifacts_dir / "verify_inventory_docs_posix_paths.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["hits"], [])

    def test_fails_with_backslashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            docs_dir = repo_root / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / "inventory.md").write_text(
                "# Inventory\n- `tools\\verify_x.py`\n",
                encoding="utf-8",
            )
            artifacts_dir = repo_root / "artifacts"
            rc = verify_inventory_docs_posix_paths.main(
                ["--artifacts-dir", str(artifacts_dir), "--repo-root", str(repo_root)]
            )
            self.assertEqual(rc, 1)
            payload = json.loads(
                (artifacts_dir / "verify_inventory_docs_posix_paths.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["status"], "FAIL")
            self.assertTrue(payload["hits"])


if __name__ == "__main__":
    unittest.main()
