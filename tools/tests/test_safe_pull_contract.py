import unittest
from pathlib import Path

from tools import safe_pull_contract


class SafePullContractTests(unittest.TestCase):
    def test_safe_pull_script_contract(self) -> None:
        script_path = Path("scripts") / "safe_pull_v1.ps1"
        status, errors = safe_pull_contract._check_contract(script_path)
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])

    def test_safe_pull_preflight_checks_present(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("git status --porcelain", content)
        self.assertIn("git ls-files -u", content)
        for state_marker in [
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-apply",
            "rebase-merge",
            "AM",
        ]:
            self.assertIn(state_marker, content)


if __name__ == "__main__":
    unittest.main()
