import unittest
from pathlib import Path

from tools import verify_powershell_null_safe_trim_contract


class VerifyPowerShellNullSafeTrimContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures_dir = (
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "verify_powershell_null_safe_trim_contract"
        )

    def test_clean_script_passes(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "clean.ps1"
        )
        self.assertEqual(offenses, [])

    def test_bad_trim_fails_with_details(self) -> None:
        path = self.fixtures_dir / "bad_trim.ps1"
        offenses = verify_powershell_null_safe_trim_contract._scan_file(path)
        self.assertEqual(len(offenses), 3)
        lines = {offense["line"] for offense in offenses}
        self.assertEqual(lines, {1, 2, 3})
        for offense in offenses:
            self.assertEqual(offense["file"], path.as_posix())
            self.assertEqual(
                offense["rule_id"],
                verify_powershell_null_safe_trim_contract.RULE_ID,
            )

    def test_good_cast_passes(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "good_cast.ps1"
        )
        self.assertEqual(offenses, [])

    def test_good_interp_passes(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "good_interp.ps1"
        )
        self.assertEqual(offenses, [])


if __name__ == "__main__":
    unittest.main()
