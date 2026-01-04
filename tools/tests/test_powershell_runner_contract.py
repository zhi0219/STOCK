import unittest
from pathlib import Path

from tools import verify_powershell_runner_contract


class PowerShellRunnerContractTests(unittest.TestCase):
    def test_powershell_runner_contract(self) -> None:
        runner_path = Path("scripts") / "powershell_runner.ps1"
        status, errors = verify_powershell_runner_contract._check_contract(runner_path)
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
