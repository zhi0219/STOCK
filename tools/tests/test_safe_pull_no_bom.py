import unittest
from pathlib import Path


class SafePullNoBomTests(unittest.TestCase):
    def test_safe_pull_fixtures_no_bom(self) -> None:
        base_dir = Path("fixtures") / "safe_pull_contract" / "good"
        for filename in ["safe_pull_markers.txt", "safe_pull_summary.json"]:
            data = (base_dir / filename).read_bytes()
            self.assertNotEqual(data[:3], b"\xef\xbb\xbf", msg=f"BOM found in {filename}")


if __name__ == "__main__":
    unittest.main()
