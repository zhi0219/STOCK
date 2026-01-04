import unittest
from pathlib import Path

from tools import safe_push_contract


class SafePushContractTests(unittest.TestCase):
    def test_safe_push_script_contract(self) -> None:
        script_path = Path("scripts") / "safe_push_v1.ps1"
        status, errors = safe_push_contract._check_contract(script_path)
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
