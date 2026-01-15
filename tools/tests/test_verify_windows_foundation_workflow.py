import tempfile
import unittest
from pathlib import Path

from tools import verify_windows_foundation_workflow


class VerifyWindowsFoundationWorkflowTests(unittest.TestCase):
    def test_workflow_contract(self) -> None:
        workflow_path = Path(".github/workflows/windows_foundation.yml")
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts_dir = Path(tmpdir)
            rc = verify_windows_foundation_workflow.main(
                ["--workflow", str(workflow_path), "--artifacts-dir", str(artifacts_dir)]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(
                (artifacts_dir / "verify_windows_foundation_workflow.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
