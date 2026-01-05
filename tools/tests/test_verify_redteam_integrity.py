import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools import verify_redteam_integrity
from tools.paths import repo_root


FIXTURES_DIR = repo_root() / "fixtures" / "redteam_integrity"


class VerifyRedteamIntegrityTests(unittest.TestCase):
    def _run_gate(self, fixtures_dir: Path) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts_dir = Path(tmp_dir) / "artifacts"
            rc = verify_redteam_integrity.main(
                [
                    "--fixtures-dir",
                    str(fixtures_dir),
                    "--artifacts-dir",
                    str(artifacts_dir),
                ]
            )
            report = json.loads(
                (artifacts_dir / "redteam_report.json").read_text(encoding="utf-8")
            )
            return rc, report

    def test_gate_reports_expected_case_statuses(self) -> None:
        rc, report = self._run_gate(FIXTURES_DIR)
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "PASS")
        cases = {case["name"]: case for case in report["cases"]}
        self.assertEqual(cases["control"]["status"], "PASS")
        self.assertEqual(cases["lookahead_feature_injection"]["status"], "FAIL")
        self.assertEqual(cases["label_misalignment"]["status"], "FAIL")
        self.assertEqual(cases["shuffled_time_order"]["status"], "FAIL")
        self.assertEqual(cases["survivorship_bias"]["status"], "FAIL")

    def test_gate_fails_without_trial_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            fixtures_copy = Path(tmp_dir) / "fixtures"
            shutil.copytree(FIXTURES_DIR, fixtures_copy)
            trial_budget_path = fixtures_copy / "trial_budget.json"
            if trial_budget_path.exists():
                trial_budget_path.unlink()
            rc, report = self._run_gate(fixtures_copy)
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("trial_budget_missing", report["summary_reasons"])


if __name__ == "__main__":
    unittest.main()
