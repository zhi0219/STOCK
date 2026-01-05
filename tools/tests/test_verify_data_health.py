import json
import tempfile
import unittest
from pathlib import Path

from tools import verify_data_health
from tools.paths import repo_root


FIXTURES_DIR = repo_root() / "fixtures" / "data_health"


class VerifyDataHealthTests(unittest.TestCase):
    def _run_health_check(self, fixture_name: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts = Path(tmp_dir) / "artifacts"
            data_path = FIXTURES_DIR / fixture_name
            rc = verify_data_health.main(
                [
                    "--data-path",
                    str(data_path),
                    "--artifacts-dir",
                    str(artifacts),
                    "--timezone",
                    "America/New_York",
                ]
            )
            report = json.loads((artifacts / "data_health_report.json").read_text(encoding="utf-8"))
            return rc, report

    def test_clean_dataset_passes(self) -> None:
        rc, report = self._run_health_check("clean.csv")
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["warnings"]), 0)

    def test_duplicate_timestamp_fails(self) -> None:
        rc, report = self._run_health_check("duplicate_timestamp.csv")
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["status"], "FAIL")
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("timestamp_duplicates", codes)

    def test_missingness_fails(self) -> None:
        rc, report = self._run_health_check("missingness_fail.csv")
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["status"], "FAIL")
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("missingness_ratio", codes)

    def test_parse_error_fails(self) -> None:
        rc, report = self._run_health_check("parse_error.csv")
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["status"], "FAIL")
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("timestamp_parse_error", codes)

    def test_warning_only_passes(self) -> None:
        rc, report = self._run_health_check("warning_only.csv")
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertGreater(len(report["warnings"]), 0)


if __name__ == "__main__":
    unittest.main()
