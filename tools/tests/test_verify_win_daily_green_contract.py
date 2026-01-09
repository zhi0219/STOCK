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


if __name__ == "__main__":
    unittest.main()
