import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import verify_foundation


class VerifyFoundationArtifactsTests(unittest.TestCase):
    def test_writes_summary_and_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts_dir = Path(tmp_dir) / "artifacts"
            gate_results = [
                verify_foundation.GateResult(
                    name="gate_a.py",
                    status="PASS",
                    returncode=0,
                    stdout="ok",
                    stderr="",
                ),
                verify_foundation.GateResult(
                    name="gate_b.py",
                    status="DEGRADED",
                    returncode=0,
                    stdout="",
                    stderr="",
                    degraded=True,
                    reason="missing_deps=pandas",
                ),
            ]

            with patch("tools.verify_foundation.GATES", ["gate_a.py", "gate_b.py"]):
                with patch("tools.verify_foundation.run_gate", side_effect=gate_results):
                    exit_code = verify_foundation.main(["--artifacts-dir", str(artifacts_dir)])

            self.assertEqual(exit_code, 0)

            summary_path = artifacts_dir / "foundation_summary.json"
            markers_path = artifacts_dir / "foundation_markers.txt"

            self.assertTrue(summary_path.exists())
            self.assertTrue(markers_path.exists())

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(
                {
                    "ts_utc",
                    "cmd",
                    "artifacts_dir",
                    "summary_status",
                    "degraded",
                    "failed",
                    "results",
                    "stdout_summary_line",
                }.issubset(summary.keys())
            )
            self.assertEqual(summary["artifacts_dir"], str(artifacts_dir.resolve()))

            markers = markers_path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any(line.startswith("FOUNDATION_START|") for line in markers))
            self.assertTrue(any(line.startswith("FOUNDATION_GATE|name=gate_a.py") for line in markers))
            self.assertTrue(any(line.startswith("FOUNDATION_SUMMARY|status=") for line in markers))
            self.assertTrue(any(line.startswith("FOUNDATION_END|exit_code=0") for line in markers))

    def test_exception_writes_exception_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifacts_dir = Path(tmp_dir) / "artifacts"
            with patch("tools.verify_foundation.GATES", ["gate_a.py"]):
                with patch("tools.verify_foundation.run_gate", side_effect=RuntimeError("boom")):
                    exit_code = verify_foundation.main(["--artifacts-dir", str(artifacts_dir)])

            self.assertEqual(exit_code, 1)

            summary_path = artifacts_dir / "foundation_summary.json"
            markers_path = artifacts_dir / "foundation_markers.txt"
            exception_path = artifacts_dir / "foundation_exception.json"

            self.assertTrue(summary_path.exists())
            self.assertTrue(markers_path.exists())
            self.assertTrue(exception_path.exists())

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertIn("exception", summary)

            exception = json.loads(exception_path.read_text(encoding="utf-8"))
            self.assertTrue(
                {
                    "ts_utc",
                    "type",
                    "message",
                    "traceback",
                }.issubset(exception.keys())
            )

            markers = markers_path.read_text(encoding="utf-8")
            self.assertIn("FOUNDATION_SUMMARY|status=FAIL", markers)
            self.assertIn("FOUNDATION_END|exit_code=1", markers)


if __name__ == "__main__":
    unittest.main()
