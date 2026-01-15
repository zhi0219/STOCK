import unittest
from pathlib import Path


class SafePullNoBomTests(unittest.TestCase):
    def test_fixture_files_have_no_bom(self) -> None:
        fixture_dir = Path("fixtures") / "safe_pull_contract" / "good"
        targets = [
            fixture_dir / "safe_pull_markers.txt",
            fixture_dir / "safe_pull_summary.json",
            fixture_dir / "safe_pull_run.json",
        ]
        for target in targets:
            data = target.read_bytes()
            self.assertFalse(
                data.startswith(b"\xef\xbb\xbf"),
                msg=f"BOM detected in {target}",
            )


if __name__ == "__main__":
    unittest.main()
