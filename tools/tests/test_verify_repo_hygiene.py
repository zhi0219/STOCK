import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools import verify_repo_hygiene


class VerifyRepoHygieneTests(unittest.TestCase):
    def test_untracked_source_like_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            artifacts_dir = repo_root / "artifacts"
            gitignore = repo_root / ".gitignore"
            gitignore.write_text("\n".join(verify_repo_hygiene.REQUIRED_RULES) + "\n", encoding="utf-8")

            subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True)

            baseline = repo_root / "README.md"
            baseline.write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo_root, check=True, capture_output=True)

            scripts_dir = repo_root / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            new_file = scripts_dir / "new_file.ps1"
            new_file.write_text("Write-Output \"hi\"\n", encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                rc = verify_repo_hygiene.main(
                    ["--repo-root", str(repo_root), "--artifacts-dir", str(artifacts_dir)]
                )

            stdout_text = output.getvalue()
            self.assertNotEqual(rc, 0)
            self.assertIn("REPO_HYGIENE_UNTRACKED", stdout_text)
            self.assertIn("REPO_HYGIENE_HINT_GIT_ADD|paths=scripts/new_file.ps1", stdout_text)
            artifact_path = artifacts_dir / "repo_hygiene_untracked.json"
            self.assertTrue(artifact_path.exists())


if __name__ == "__main__":
    unittest.main()
