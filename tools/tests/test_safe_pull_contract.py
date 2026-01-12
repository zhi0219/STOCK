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
        self.assertIn("ls-files -u", content)
        for state_marker in [
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-apply",
            "rebase-merge",
            "AM",
        ]:
            self.assertIn(state_marker, content)

    def test_safe_pull_stash_contract(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        for marker in [
            "SAFE_PULL_STASH",
        ]:
            self.assertIn(marker, content)
        self.assertIn('\"stash\", \"push\"', content)
        self.assertIn("stash_ref.txt", content)

    def test_safe_pull_default_dry_run(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("[bool]$DryRun = $true", content)
        self.assertIn("dirty_worktree_dry_run", content)

    def test_safe_pull_run_git_null_safe(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertNotIn("($stdoutText + $stderrText).Trim()", content)
        self.assertIn("[string]::Concat($stdoutText, $stderrText).Trim()", content)
        self.assertIn('if ($null -eq $stdoutText) { $stdoutText = "" }', content)
        self.assertIn('if ($null -eq $stderrText) { $stderrText = "" }', content)

    def test_safe_pull_fs_run_id_sanitization(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("$script:FsRunId", content)
        self.assertIn("GetInvalidFileNameChars()", content)

    def test_safe_pull_no_bom_writer(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("Write-TextNoBom", content)
        self.assertIn("UTF8Encoding $false", content)

    def test_safe_pull_allowlist_fallback_marker(self) -> None:
        content = (Path("scripts") / "safe_pull_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("artifacts_dir_outside_allowlist", content)
        self.assertIn("SAFE_PULL_ARTIFACTS_FALLBACK", content)


if __name__ == "__main__":
    unittest.main()
