import unittest
from pathlib import Path


def _assert_no_bom(path: Path) -> None:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise AssertionError(f"BOM detected in {path}")


class SafePullNoBomTests(unittest.TestCase):
    def test_markers_no_bom(self) -> None:
        path = Path("fixtures") / "safe_pull_contract" / "good" / "safe_pull_markers.txt"
        _assert_no_bom(path)

    def test_summary_no_bom(self) -> None:
        path = Path("fixtures") / "safe_pull_contract" / "good" / "safe_pull_summary.json"
        _assert_no_bom(path)

    def test_exception_no_bom(self) -> None:
        path = (
            Path("fixtures")
            / "safe_pull_contract"
            / "good"
            / "exception_internal"
            / "safe_pull_exception.txt"
        )
        _assert_no_bom(path)


if __name__ == "__main__":
    unittest.main()
