import re
import tempfile
import unittest
from pathlib import Path

from tools import verify_safe_pull_contract


class VerifySafePullContractTests(unittest.TestCase):
    def test_safe_pull_no_ordered_dot_assignments(self) -> None:
        script_path = Path("scripts") / "safe_pull_v1.ps1"
        content = script_path.read_text(encoding="utf-8", errors="replace")
        self.assertIsNone(
            re.search(r"\$script:RunPayload\.", content),
            msg="RunPayload must use indexer assignment",
        )
        self.assertIsNone(
            re.search(r"\.phases\s*=", content),
            msg="phases assignment must use indexer assignment",
        )

    def test_good_fixture_passes(self) -> None:
        base_dir = Path("fixtures") / "safe_pull_contract" / "good"
        fixture_dirs = [base_dir] + sorted(
            path for path in base_dir.iterdir() if path.is_dir()
        )
        for fixture_dir in fixture_dirs:
            with tempfile.TemporaryDirectory() as tmpdir:
                artifacts_dir = Path(tmpdir)
                rc = verify_safe_pull_contract.main(
                    [
                        "--artifacts-dir",
                        str(artifacts_dir),
                        "--input-dir",
                        str(fixture_dir),
                    ]
                )
                self.assertEqual(rc, 0, msg=f"fixture failed: {fixture_dir}")
                self.assertTrue(
                    (artifacts_dir / "verify_safe_pull_contract.json").exists()
                )

    def test_bad_fixture_fails(self) -> None:
        base_dir = Path("fixtures") / "safe_pull_contract" / "bad"
        fixture_dirs = [base_dir] + sorted(
            path for path in base_dir.iterdir() if path.is_dir()
        )
        for fixture_dir in fixture_dirs:
            with tempfile.TemporaryDirectory() as tmpdir:
                artifacts_dir = Path(tmpdir)
                rc = verify_safe_pull_contract.main(
                    [
                        "--artifacts-dir",
                        str(artifacts_dir),
                        "--input-dir",
                        str(fixture_dir),
                    ]
                )
                self.assertNotEqual(rc, 0, msg=f"fixture passed: {fixture_dir}")


if __name__ == "__main__":
    unittest.main()
