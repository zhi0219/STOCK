import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from tools import verify_pr_ready


class VerifyPrReadyTests(unittest.TestCase):
    def test_pr_ready_degraded_allows_exit_zero(self) -> None:
        procs = [
            SimpleNamespace(returncode=0, stdout="compile ok", stderr=""),
            SimpleNamespace(returncode=0, stdout="docs ok", stderr=""),
            SimpleNamespace(returncode=0, stdout="inventory ok", stderr=""),
            SimpleNamespace(returncode=0, stdout="foundation ok", stderr=""),
            SimpleNamespace(
                returncode=1,
                stdout="CONSISTENCY_SUMMARY|status=DEGRADED|skipped=x",
                stderr="",
            ),
        ]

        with TemporaryDirectory() as tempdir:
            with mock.patch("tools.verify_pr_ready.run_cmd_utf8", side_effect=procs):
                with mock.patch("tools.verify_pr_ready.configure_stdio_utf8"):
                    with redirect_stdout(io.StringIO()) as buffer:
                        exit_code = verify_pr_ready.main(["--artifacts-dir", tempdir])
                        output = buffer.getvalue()

            self.assertIn("PR_READY_START", output)
            self.assertIn("PR_READY_END", output)
            self.assertIn("PR_READY_SUMMARY|status=DEGRADED", output)

            summary_path = Path(tempdir) / "pr_ready_summary.json"
            text_path = Path(tempdir) / "pr_ready.txt"
            log_path = Path(tempdir) / "pr_ready_gates.log"
            self.assertTrue(summary_path.exists())
            self.assertTrue(text_path.exists())
            self.assertTrue(log_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "DEGRADED")
            self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
