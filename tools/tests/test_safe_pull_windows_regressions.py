import unittest
from pathlib import Path


class SafePullWindowsRegressionTests(unittest.TestCase):
    def test_run_id_fs_safe_no_invalid_chars(self) -> None:
        content = Path("scripts/safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("GetInvalidFileNameChars", content)
        self.assertIn("RunIdFs", content)
        run_id = "2026-01-12T07:51:12Z-2996"
        invalid_chars = '<>:"/\\|?*'
        sanitized = "".join("_" if ch in invalid_chars else ch for ch in run_id)
        for ch in invalid_chars:
            self.assertNotIn(ch, sanitized)

    def test_trimend_char_overload_no_argument_exception(self) -> None:
        content = Path("scripts/safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn(".TrimEnd('\\', '/')", content)
        self.assertIn(".TrimStart('\\', '/')", content)
        self.assertNotIn('.TrimEnd("\\", "/")', content)
        self.assertNotIn('.TrimStart("\\", "/")', content)

    def test_no_bom_outputs_for_markers_and_summary(self) -> None:
        content = Path("scripts/safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("UTF8Encoding $false", content)
        self.assertIn("[IO.File]::WriteAllText", content)
        self.assertIn("[IO.File]::AppendAllText", content)
        self.assertNotIn("Set-Content -LiteralPath $script:MarkersPath", content)


if __name__ == "__main__":
    unittest.main()
