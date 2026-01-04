import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import git_health, repo_hygiene


class GitHealthTests(unittest.TestCase):
    def _init_repo(self, root: Path) -> None:
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )

    def _patch_roots(self, root: Path) -> contextlib.ExitStack:
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(git_health, "repo_root", return_value=root))
        stack.enter_context(mock.patch.object(repo_hygiene, "repo_root", return_value=root))
        stack.enter_context(mock.patch("tools.paths.repo_root", return_value=root))
        return stack

    def test_report_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as artifacts_dir:
            root = Path(repo_dir)
            self._init_repo(root)
            logs_dir = root / "Logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            runtime_file = logs_dir / "supervisor.log"
            runtime_file.write_text("runtime", encoding="utf-8")
            before_contents = runtime_file.read_text(encoding="utf-8")

            with self._patch_roots(root):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = git_health.main(
                        ["report", "--artifacts-dir", artifacts_dir]
                    )

            self.assertEqual(before_contents, runtime_file.read_text(encoding="utf-8"))
            self.assertTrue(runtime_file.exists())
            self.assertIn(exit_code, {0, 1})

    def test_fix_skips_locked_files(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as artifacts_dir:
            root = Path(repo_dir)
            self._init_repo(root)
            gitignore = root / ".gitignore"
            gitignore.write_text("Logs/\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            logs_dir = root / "Logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            runtime_file = logs_dir / "runtime_lock_test.log"
            runtime_file.write_text("lock", encoding="utf-8")

            original_unlink = Path.unlink

            def _side_effect(self: Path) -> None:
                if self == runtime_file:
                    raise PermissionError("locked")
                return original_unlink(self)

            with self._patch_roots(root):
                with mock.patch.object(Path, "unlink", _side_effect):
                    with contextlib.redirect_stdout(io.StringIO()):
                        exit_code = git_health.main(
                            ["fix", "--artifacts-dir", artifacts_dir]
                        )

            self.assertEqual(exit_code, 0)
            report_path = Path(artifacts_dir) / git_health.ARTIFACT_JSON_NAME
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn("locked_files", payload)
            self.assertIn("Logs/runtime_lock_test.log", payload["locked_files"])

    def test_report_markers_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as artifacts_dir:
            root = Path(repo_dir)
            self._init_repo(root)
            (root / "dirty.txt").write_text("dirty", encoding="utf-8")

            buffer = io.StringIO()
            with self._patch_roots(root):
                with contextlib.redirect_stdout(buffer):
                    exit_code = git_health.main(
                        ["report", "--artifacts-dir", artifacts_dir]
                    )

            output = buffer.getvalue()
            self.assertIn("GIT_HEALTH_START", output)
            self.assertIn("GIT_HEALTH_SUMMARY|", output)
            self.assertIn("next=", output)
            self.assertIn("GIT_HEALTH_END", output)
            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
