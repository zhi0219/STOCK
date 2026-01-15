import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class SafePullNoBomArtifactTests(unittest.TestCase):
    def _find_powershell(self) -> str | None:
        return shutil.which("pwsh") or shutil.which("powershell")

    def test_safe_pull_artifacts_no_bom(self) -> None:
        ps_exe = self._find_powershell()
        git_exe = shutil.which("git")
        if ps_exe is None or git_exe is None:
            self.skipTest("powershell or git not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            origin_dir = root / "origin.git"
            repo_dir.mkdir()

            subprocess.run([git_exe, "init"], cwd=repo_dir, check=True)
            subprocess.run([git_exe, "checkout", "-b", "master"], cwd=repo_dir, check=True)
            subprocess.run(
                [git_exe, "config", "user.email", "ci@example.com"],
                cwd=repo_dir,
                check=True,
            )
            subprocess.run(
                [git_exe, "config", "user.name", "CI"], cwd=repo_dir, check=True
            )
            (repo_dir / "README.txt").write_text("hello", encoding="utf-8")
            subprocess.run([git_exe, "add", "README.txt"], cwd=repo_dir, check=True)
            subprocess.run(
                [git_exe, "commit", "-m", "init"], cwd=repo_dir, check=True
            )

            subprocess.run([git_exe, "init", "--bare", str(origin_dir)], check=True)
            subprocess.run(
                [git_exe, "remote", "add", "origin", str(origin_dir)],
                cwd=repo_dir,
                check=True,
            )
            subprocess.run(
                [git_exe, "push", "-u", "origin", "master"],
                cwd=repo_dir,
                check=True,
            )

            artifacts_dir = repo_dir / "artifacts" / "safe_pull"
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            script_path = Path("scripts") / "safe_pull_v1.ps1"
            command = [
                ps_exe,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-RepoRoot",
                str(repo_dir),
                "-ArtifactsDir",
                str(artifacts_dir),
                "-Mode",
                "dry_run",
                "-DryRun",
                "1",
                "-ExpectedUpstream",
                "origin/master",
                "-ExpectedRemotePattern",
                ".*",
            ]
            subprocess.run(command, cwd=repo_dir, check=True)

            markers_path = artifacts_dir / "safe_pull_markers.txt"
            summary_path = artifacts_dir / "safe_pull_summary.json"

            for path in (markers_path, summary_path):
                data = path.read_bytes()
                self.assertFalse(
                    data.startswith(b"\xef\xbb\xbf"),
                    msg=f"BOM detected in {path}",
                )


if __name__ == "__main__":
    unittest.main()
