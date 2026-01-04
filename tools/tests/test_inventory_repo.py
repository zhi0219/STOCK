import json
import tempfile
import unittest
from pathlib import Path

from tools import inventory_repo


class InventoryRepoTests(unittest.TestCase):
    def test_deterministic_output(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts_dir = Path(tmp_dir) / "artifacts"
            rc_first = inventory_repo.main(
                ["--artifacts-dir", str(artifacts_dir), "--repo-root", str(root)]
            )
            self.assertEqual(rc_first, 0)
            first_json = (artifacts_dir / "repo_inventory.json").read_text(encoding="utf-8")

            rc_second = inventory_repo.main(
                ["--artifacts-dir", str(artifacts_dir), "--repo-root", str(root)]
            )
            self.assertEqual(rc_second, 0)
            second_json = (artifacts_dir / "repo_inventory.json").read_text(encoding="utf-8")

            self.assertEqual(first_json, second_json)

    def test_required_sections_exist(self) -> None:
        root = Path(__file__).resolve().parents[2]
        inventory = inventory_repo.generate_inventory(root)
        for key in ["version", "entrypoints", "gates", "artifacts", "contracts", "feature_map"]:
            self.assertIn(key, inventory)

    def test_thresholds(self) -> None:
        root = Path(__file__).resolve().parents[2]
        inventory = inventory_repo.generate_inventory(root)
        self.assertGreaterEqual(len(inventory["entrypoints"]), 5)
        self.assertGreaterEqual(len(inventory["gates"]), 3)


if __name__ == "__main__":
    unittest.main()
