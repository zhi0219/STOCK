import shutil
import tempfile
import unittest
from pathlib import Path

from tools import verify_safe_pull_contract


class VerifySafePullContractTests(unittest.TestCase):
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

    def test_root_latest_layout(self) -> None:
        fixture_dir = Path("fixtures") / "safe_pull_contract" / "good"
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts_root = Path(tmpdir) / "artifacts"
            run_dir = artifacts_root / "safe_pull" / "run-123"
            shutil.copytree(fixture_dir, run_dir, dirs_exist_ok=True)
            latest_path = artifacts_root / "safe_pull" / "_latest.txt"
            latest_path.write_text(run_dir.as_posix(), encoding="utf-8")
            rc = verify_safe_pull_contract.main(
                [
                    "--artifacts-dir",
                    str(artifacts_root / "contract"),
                    "--input-dir",
                    str(artifacts_root),
                ]
            )
            self.assertEqual(rc, 0)

    def test_run_dir_layout(self) -> None:
        fixture_dir = Path("fixtures") / "safe_pull_contract" / "good"
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "safe_pull_run"
            shutil.copytree(fixture_dir, run_dir, dirs_exist_ok=True)
            rc = verify_safe_pull_contract.main(
                [
                    "--artifacts-dir",
                    str(Path(tmpdir) / "contract"),
                    "--input-dir",
                    str(run_dir),
                ]
            )
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
