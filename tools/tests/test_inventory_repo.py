import json
import re
import tempfile
import unittest
from pathlib import Path

from tools import inventory_repo


class InventoryRepoTests(unittest.TestCase):
    def test_canonical_repo_path_normalizes_windows_separators(self) -> None:
        self.assertEqual(inventory_repo._canonical_repo_path("tools\\x.py"), "tools/x.py")

    def test_canonical_repo_path_handles_mixed_separators(self) -> None:
        self.assertEqual(inventory_repo._canonical_repo_path("tools/dir\\x.py"), "tools/dir/x.py")

    def test_canonical_repo_path_preserves_posix(self) -> None:
        self.assertEqual(inventory_repo._canonical_repo_path("tools/x.py"), "tools/x.py")

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

    def test_write_text_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "out.txt"
            inventory_repo._write_text(path, "line1\r\nline2\rline3")
            data = path.read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", data)

    def test_inventory_paths_have_no_backslashes(self) -> None:
        root = Path(__file__).resolve().parents[2]
        inventory = inventory_repo.generate_inventory(root)
        path_fields = []
        path_fields.extend(entry["path"] for entry in inventory["entrypoints"])
        path_fields.extend(artifact["path_glob"] for artifact in inventory["artifacts"])
        path_fields.extend(contract["docs_path"] for contract in inventory["contracts"])
        for feature in inventory["feature_map"]:
            path_fields.extend(feature["files"])
        for value in path_fields:
            self.assertNotIn("\\", value)

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


if __name__ == "__main__":
    unittest.main()
