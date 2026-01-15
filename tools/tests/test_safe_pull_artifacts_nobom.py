import unittest
from pathlib import Path


class SafePullArtifactsNoBomTests(unittest.TestCase):
    def test_safe_pull_fixtures_have_no_bom(self) -> None:
        base_dir = Path("fixtures") / "safe_pull_contract"
        fixture_dirs = [base_dir] + sorted(
            path for path in base_dir.rglob("*") if path.is_dir()
        )
        for fixture_dir in fixture_dirs:
            for artifact in fixture_dir.iterdir():
                if not artifact.is_file():
                    continue
                data = artifact.read_bytes()
                if len(data) < 3:
                    continue
                self.assertNotEqual(
                    data[:3],
                    b"\xef\xbb\xbf",
                    msg=f"bom detected in {artifact}",
                )


if __name__ == "__main__":
    unittest.main()
