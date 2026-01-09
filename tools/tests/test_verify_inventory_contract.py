import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools import verify_inventory_contract
from tools import inventory_repo


class VerifyInventoryContractTests(unittest.TestCase):
    def test_normalized_compare_allows_crlf(self) -> None:
        expected = "line-one\nline-two\n"
        actual = "line-one\r\nline-two\r\n"
        self.assertTrue(verify_inventory_contract._normalized_equal(actual, expected))

    def test_verify_emits_eol_stats_on_failure(self) -> None:
        root = Path(__file__).resolve().parents[2]
        docs_path = root / "docs" / "inventory.md"
        original = docs_path.read_bytes()
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts_dir = Path(tmp_dir) / "artifacts"
            expected = inventory_repo._render_markdown(inventory_repo.generate_inventory(root))
            expected_bytes = expected.encode("utf-8")
            crlf_bytes = original.replace(b"\n", b"\r\n")
            try:
                docs_path.write_bytes(crlf_bytes)
                output = io.StringIO()
                with redirect_stdout(output):
                    rc = verify_inventory_contract.main(
                        [
                            "--artifacts-dir",
                            str(artifacts_dir),
                            "--repo-root",
                            str(root),
                        ]
                    )
                self.assertNotEqual(rc, 0)
                stats_path = artifacts_dir / "verify_inventory_eol_stats.json"
                self.assertTrue(stats_path.exists())
                stats = json.loads(stats_path.read_text(encoding="utf-8"))
                self.assertEqual(stats["docs_crlf_pairs"], crlf_bytes.count(b"\r\n"))
                self.assertEqual(stats["docs_len"], len(crlf_bytes))
                self.assertEqual(stats["gen_len"], len(expected_bytes))
                self.assertEqual(stats["gen_crlf_pairs"], 0)
                self.assertEqual(stats["verdict"], "FAIL")
                self.assertIn("VERIFY_INVENTORY_EOLS", output.getvalue())
            finally:
                docs_path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
