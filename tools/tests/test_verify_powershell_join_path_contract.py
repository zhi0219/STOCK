import tempfile
import unittest
from pathlib import Path

from tools import verify_powershell_join_path_contract


class VerifyPowerShellJoinPathContractTests(unittest.TestCase):
    def _write_script(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
        return path

    def test_allows_nested_join_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_script(
                root,
                "ok.ps1",
                'Set-Content -Path (Join-Path (Join-Path $root ".git") "HEAD") -Value ""',
            )
            status, offenses = verify_powershell_join_path_contract._check_contract(root)
            self.assertEqual(status, "PASS")
            self.assertEqual(offenses, [])

    def test_rejects_three_positional_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_script(
                root,
                "bad.ps1",
                'Set-Content -Path (Join-Path $root ".git" "HEAD") -Value ""',
            )
            status, offenses = verify_powershell_join_path_contract._check_contract(root)
            self.assertEqual(status, "FAIL")
            self.assertTrue(
                any(offense["rule"] == "join_path_positional_args" for offense in offenses)
            )

    def test_rejects_additional_child_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_script(
                root,
                "bad.ps1",
                'Join-Path -Path $root -ChildPath ".git" -AdditionalChildPath "HEAD"',
            )
            status, offenses = verify_powershell_join_path_contract._check_contract(root)
            self.assertEqual(status, "FAIL")
            self.assertTrue(
                any(
                    offense["rule"] == "join_path_additional_child_path"
                    for offense in offenses
                )
            )


if __name__ == "__main__":
    unittest.main()
