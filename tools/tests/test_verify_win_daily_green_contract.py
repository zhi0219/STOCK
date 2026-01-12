import tempfile
import unittest
from pathlib import Path

from tools import verify_win_daily_green_contract


class VerifyWinDailyGreenContractTests(unittest.TestCase):
    def test_daily_green_script_contract(self) -> None:
        script_path = Path("scripts") / "win_daily_green_v1.ps1"
        status, errors = verify_win_daily_green_contract._check_contract(script_path)
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])

    def test_marker_output_contract(self) -> None:
        mocked_output = [
            "DAILY_GREEN_START|ts_utc=2024-01-01T00:00:00Z|repo_root=/repo|artifacts_dir=/repo/artifacts|run_dir=/repo/artifacts/run|python=python.exe|autostash=YES",
            "DAILY_GREEN_STEP|name=safe_pull|status=PASS|exit_code=0|stdout=out.txt|stderr=err.txt",
            "DAILY_GREEN_SUMMARY|status=PASS|failed_step=none|next=none|run_dir=/repo/artifacts/run",
            "DAILY_GREEN_END",
        ]
        ok, errors = verify_win_daily_green_contract.validate_marker_output(mocked_output)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_contract_accepts_legacy_dryrun(self) -> None:
        script_path = Path("scripts") / "win_daily_green_v1.ps1"
        content = script_path.read_text(encoding="utf-8", errors="replace")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_script = Path(temp_dir) / "win_daily_green_legacy.ps1"
            temp_script.write_text(content, encoding="utf-8")
            status, errors = verify_win_daily_green_contract._check_contract(
                temp_script
            )
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])

    def test_contract_accepts_mode_dry_run(self) -> None:
        script_path = Path("scripts") / "win_daily_green_v1.ps1"
        content = script_path.read_text(encoding="utf-8", errors="replace")
        content = content.replace('"-DryRun:$false"', '"-Mode",\n  "dry_run"')
        content = content.replace('"-DryRun"', '"-Mode",\n  "dry_run"')
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_script = Path(temp_dir) / "win_daily_green_mode.ps1"
            temp_script.write_text(content, encoding="utf-8")
            status, errors = verify_win_daily_green_contract._check_contract(
                temp_script
            )
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])

    def test_contract_rejects_missing_dry_run_flags(self) -> None:
        script_path = Path("scripts") / "win_daily_green_v1.ps1"
        content = script_path.read_text(encoding="utf-8", errors="replace")
        content = content.replace('"-DryRun:$false"', "")
        content = content.replace('"-DryRun"', "")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_script = Path(temp_dir) / "win_daily_green_missing.ps1"
            temp_script.write_text(content, encoding="utf-8")
            status, errors = verify_win_daily_green_contract._check_contract(
                temp_script
            )
        self.assertEqual(status, "FAIL")
        self.assertIn("missing_command_pattern:dry_run_flag", errors)


if __name__ == "__main__":
    unittest.main()
