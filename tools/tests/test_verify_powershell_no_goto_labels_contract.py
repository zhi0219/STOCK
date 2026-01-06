import tempfile
import unittest
from pathlib import Path

from tools import verify_powershell_no_goto_labels_contract


class VerifyPowerShellNoGotoLabelsContractTests(unittest.TestCase):
    def _write_script(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
        return path

    def test_passes_clean_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_script(
                root,
                "scripts/ok.ps1",
                "Write-Host 'ok'",
            )
            status, offenses = verify_powershell_no_goto_labels_contract._check_contract(
                root
            )
            self.assertEqual(status, "PASS")
            self.assertEqual(offenses, [])

    def test_rejects_goto_statement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_script(
                root,
                "scripts/bad.ps1",
                "goto PsRunnerFinalize",
            )
            status, offenses = verify_powershell_no_goto_labels_contract._check_contract(
                root
            )
            self.assertEqual(status, "FAIL")
            self.assertTrue(
                any(offense["rule"] == "goto_statement" for offense in offenses)
            )

    def test_rejects_bare_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_script(
                root,
                "scripts/bad_label.ps1",
                ":PsRunnerFinalize",
            )
            status, offenses = verify_powershell_no_goto_labels_contract._check_contract(
                root
            )
            self.assertEqual(status, "FAIL")
            self.assertTrue(any(offense["rule"] == "bare_label" for offense in offenses))


if __name__ == "__main__":
    unittest.main()
