import json
import re
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

    def test_deterministic_markdown_output(self) -> None:
        root = Path(__file__).resolve().parents[2]
        first_md = inventory_repo._render_markdown(inventory_repo.generate_inventory(root))
        second_md = inventory_repo._render_markdown(inventory_repo.generate_inventory(root))
        self.assertEqual(first_md, second_md)

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

    def test_inventory_has_no_timestamp_lines(self) -> None:
        root = Path(__file__).resolve().parents[2]
        markdown = inventory_repo._render_markdown(inventory_repo.generate_inventory(root))
        timestamp_patterns = [
            r"\b20\d{2}-\d{2}-\d{2}\b",
            r"\b\d{2}:\d{2}:\d{2}\b",
            r"timestamp",
            r"generated on",
        ]
        for pattern in timestamp_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, markdown, re.IGNORECASE))

    def test_normalize_doc_path_rejects_absolute(self) -> None:
        with self.assertRaises(ValueError):
            inventory_repo._normalize_doc_path("/absolute/path")
        with self.assertRaises(ValueError):
            inventory_repo._normalize_doc_path("C:\\absolute\\path")
        with self.assertRaises(ValueError):
            inventory_repo._normalize_doc_path("\\\\server\\share\\path")

    def test_normalize_doc_path_uses_posix_separators(self) -> None:
        self.assertEqual(
            inventory_repo._normalize_doc_path("tools\\verify_inventory_contract.py"),
            "tools/verify_inventory_contract.py",
        )


if __name__ == "__main__":
    unittest.main()
