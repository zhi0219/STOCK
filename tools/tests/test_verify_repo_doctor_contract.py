import unittest
from pathlib import Path

from tools import verify_repo_doctor_contract


class VerifyRepoDoctorContractTests(unittest.TestCase):
    def test_repo_doctor_script_contract(self) -> None:
        script_path = Path("scripts") / "repo_doctor_v1.ps1"
        status, errors = verify_repo_doctor_contract._check_contract(script_path)
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])

    def test_contract_pass_fixture(self) -> None:
        script_path = Path("fixtures") / "repo_doctor_contract" / "good.ps1"
        status, errors = verify_repo_doctor_contract._check_contract(script_path)
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])

    def test_contract_fails_missing_marker(self) -> None:
        script_path = Path("fixtures") / "repo_doctor_contract" / "missing_marker.ps1"
        status, errors = verify_repo_doctor_contract._check_contract(script_path)
        self.assertEqual(status, "FAIL")
        self.assertTrue(any("missing_marker:REPO_DOCTOR_SUMMARY" in err for err in errors))

    def test_contract_fails_disallowed_command(self) -> None:
        script_path = Path("fixtures") / "repo_doctor_contract" / "disallowed.ps1"
        status, errors = verify_repo_doctor_contract._check_contract(script_path)
        self.assertEqual(status, "FAIL")
        self.assertTrue(any("disallowed_command_pattern" in err for err in errors))

    def test_marker_output_contract(self) -> None:
        mocked_output = [
            "REPO_DOCTOR_START|ts_utc=2024-01-01T00:00:00Z|cwd=/repo|repo_root=/repo|artifacts_dir=/repo/artifacts",
            "REPO_DOCTOR_CONFIG|write_docs=NO|python=/repo/.venv/Scripts/python.exe|repo_root=/repo|artifacts_dir=/repo/artifacts",
            "REPO_DOCTOR_STEP|name=inventory_repo|status=PASS|exit_code=0",
            "REPO_DOCTOR_STEP|name=verify_pr_ready|status=PASS|exit_code=0",
            "REPO_DOCTOR_CLEAN_POST|status=PASS|reason=ok",
            "REPO_DOCTOR_SUMMARY|status=PASS|failed_step=none|next=none",
            "REPO_DOCTOR_END",
        ]
        ok, errors = verify_repo_doctor_contract.validate_marker_output(mocked_output)
        self.assertTrue(ok)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
