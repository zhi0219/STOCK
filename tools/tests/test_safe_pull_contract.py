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

    def test_safe_pull_autostash_contract(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn('[string]$AutoStash = "NO"', content)
        for marker in [
            "SAFE_PULL_AUTOSTASH_START",
            "SAFE_PULL_AUTOSTASH_SUMMARY",
            "SAFE_PULL_AUTOSTASH_END",
        ]:
            self.assertIn(marker, content)
        self.assertIn("git stash push -u -m", content)
        self.assertIn("git stash pop", content)
        self.assertIn("safe_pull_autostash.json", content)

    def test_safe_pull_autostash_default_rejects_dirty(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("dirty_worktree", content)
        self.assertIn("if (-not $autoStashEnabled)", content)

    def test_safe_pull_autostash_rollback_contract(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("SAFE_PULL_AUTOSTASH_POP", content)
        self.assertIn("rollback_status", content)


if __name__ == "__main__":
    unittest.main()
