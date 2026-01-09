import unittest
from pathlib import Path

from tools import verify_write_allowlist_contract


class VerifyWriteAllowlistContractTests(unittest.TestCase):
    def test_daily_green_allowlist_passes(self) -> None:
        status, errors = verify_write_allowlist_contract._check_contract()
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])

    def test_allowlist_rejects_non_artifacts_write(self) -> None:
        bad_script = Path("fixtures") / "write_allowlist_contract" / "bad.ps1"
        errors = verify_write_allowlist_contract._check_script(bad_script)
        self.assertTrue(errors)
        self.assertTrue(any("write_outside_allowlist" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
