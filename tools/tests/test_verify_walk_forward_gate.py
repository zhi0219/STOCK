import json
import tempfile
import unittest
from pathlib import Path

from tools import verify_walk_forward
from tools.paths import repo_root


FIXTURE_PATH = repo_root() / "fixtures" / "walk_forward" / "ohlcv.csv"


class VerifyWalkForwardGateTests(unittest.TestCase):
    def test_gate_passes_with_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts_dir = Path(tmp_dir) / "artifacts"
            rc = verify_walk_forward.main(
                [
                    "--artifacts-dir",
                    str(artifacts_dir),
                    "--input",
                    str(FIXTURE_PATH),
                    "--gap-size",
                    "2",
                ]
            )
            self.assertEqual(rc, 0)
            report = json.loads(
                (artifacts_dir / "walk_forward_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["status"], "PASS")
            self.assertGreater(report["window_count"], 0)
            self.assertTrue(report.get("data_hash"))
            self.assertTrue(report.get("code_hash"))

    def test_gate_fails_on_zero_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts_dir = Path(tmp_dir) / "artifacts"
            rc = verify_walk_forward.main(
                [
                    "--artifacts-dir",
                    str(artifacts_dir),
                    "--input",
                    str(FIXTURE_PATH),
                    "--gap-size",
                    "0",
                ]
            )
            self.assertNotEqual(rc, 0)
            report = json.loads(
                (artifacts_dir / "walk_forward_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("gap_required", report["reasons"])


if __name__ == "__main__":
    unittest.main()
