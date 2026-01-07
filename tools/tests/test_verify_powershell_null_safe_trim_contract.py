import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
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

    def test_repo_doctor_unsafe_trim_fails(self) -> None:
        path = self.fixtures_dir / "unsafe_repo_doctor.ps1"
        offenses = verify_powershell_null_safe_trim_contract._scan_file(path)
        self.assertEqual(len(offenses), 1)
        self.assertEqual(offenses[0]["line"], 1)

    def test_unsafe_cast_trim_fails(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "unsafe_cast.ps1"
        )
        self.assertEqual(len(offenses), 1)
        self.assertEqual(offenses[0]["line"], 1)

    def test_unsafe_plain_trim_fails(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "unsafe_plain.ps1"
        )
        self.assertEqual(len(offenses), 1)
        self.assertEqual(offenses[0]["line"], 1)

    def test_safe_concat_passes(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "safe_concat.ps1"
        )
        self.assertEqual(offenses, [])

    def test_safe_null_guard_passes(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "safe_null_guard.ps1"
        )
        self.assertEqual(offenses, [])

    def test_repo_doctor_fixed_trim_passes(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "fixed_repo_doctor.ps1"
        )
        self.assertEqual(offenses, [])

    def test_redteam_multiline_trim_fails(self) -> None:
        offenses = verify_powershell_null_safe_trim_contract._scan_file(
            self.fixtures_dir / "redteam_multiline.ps1"
        )
        self.assertEqual(len(offenses), 1)
        self.assertEqual(offenses[0]["line"], 4)

    def test_contract_writes_pass_artifacts_and_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "scripts").mkdir()
            (root / "scripts" / "sample.ps1").write_text(
                "$clean = [string]::Concat($a, $b).Trim()\n",
                encoding="utf-8",
            )
            artifacts_dir = root / "artifacts"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = verify_powershell_null_safe_trim_contract.main(
                    [
                        "--root",
                        str(root),
                        "--artifacts-dir",
                        str(artifacts_dir),
                    ]
                )
            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("VERIFY_POWERSHELL_NULL_SAFE_TRIM_START", output)
            self.assertIn("VERIFY_POWERSHELL_NULL_SAFE_TRIM_SUMMARY|status=PASS", output)
            self.assertIn("VERIFY_POWERSHELL_NULL_SAFE_TRIM_END", output)
            report_path = artifacts_dir / "verify_powershell_null_safe_trim_contract.txt"
            json_path = artifacts_dir / "verify_powershell_null_safe_trim_contract.json"
            self.assertTrue(report_path.exists())
            self.assertEqual(report_path.read_text(encoding="utf-8").strip(), "ok")
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "PASS")

    def test_contract_writes_fail_artifacts_and_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "scripts").mkdir()
            (root / "scripts" / "sample.ps1").write_text(
                "$clean = ($a + $b).Trim()\n",
                encoding="utf-8",
            )
            artifacts_dir = root / "artifacts"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = verify_powershell_null_safe_trim_contract.main(
                    [
                        "--root",
                        str(root),
                        "--artifacts-dir",
                        str(artifacts_dir),
                    ]
                )
            output = stdout.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("VERIFY_POWERSHELL_NULL_SAFE_TRIM_START", output)
            self.assertIn("VERIFY_POWERSHELL_NULL_SAFE_TRIM_HIT", output)
            self.assertIn("VERIFY_POWERSHELL_NULL_SAFE_TRIM_SUMMARY|status=FAIL", output)
            self.assertIn("VERIFY_POWERSHELL_NULL_SAFE_TRIM_END", output)
            report_path = artifacts_dir / "verify_powershell_null_safe_trim_contract.txt"
            json_path = artifacts_dir / "verify_powershell_null_safe_trim_contract.json"
            self.assertTrue(report_path.exists())
            self.assertTrue(json_path.exists())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
