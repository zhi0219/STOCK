import tempfile
import unittest
from pathlib import Path

from tools import verify_safe_pull_contract


class VerifySafePullContractTests(unittest.TestCase):
    def test_good_fixture_passes(self) -> None:
        fixture_dir = Path("fixtures") / "safe_pull_contract" / "good"
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
            self.assertEqual(rc, 0)
            self.assertTrue((artifacts_dir / "verify_safe_pull_contract.json").exists())

    def test_bad_fixture_fails(self) -> None:
        fixture_dir = Path("fixtures") / "safe_pull_contract" / "bad"
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
            self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
