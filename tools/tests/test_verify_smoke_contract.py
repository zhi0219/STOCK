import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class VerifySmokeContractTests(unittest.TestCase):
    def _run_smoke(self, artifacts_dir: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        cmd = [
            sys.executable,
            "-m",
            "tools.verify_smoke",
            "--artifacts-dir",
            str(artifacts_dir),
        ]
        return subprocess.run(
            cmd,
            cwd=self._repo_root(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _assert_artifacts(self, artifacts_dir: Path) -> dict:
        json_path = artifacts_dir / "verify_smoke.json"
        txt_path = artifacts_dir / "verify_smoke.txt"
        self.assertTrue(json_path.exists(), f"missing {json_path}")
        self.assertTrue(txt_path.exists(), f"missing {txt_path}")
        return json.loads(json_path.read_text(encoding="utf-8"))

    def test_smoke_contract_accepts_artifacts_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts_dir = Path(tmpdir) / "artifacts"
            proc = self._run_smoke(artifacts_dir)
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertNotIn("unrecognized arguments: --artifacts-dir", combined)
            payload = self._assert_artifacts(artifacts_dir)
            status = payload.get("status")
            self.assertIn(status, {"PASS", "FAIL"})
            if status == "PASS":
                self.assertEqual(proc.returncode, 0)
            else:
                self.assertNotEqual(proc.returncode, 0)

    def test_smoke_contract_fail_is_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts_dir = Path(tmpdir) / "artifacts"
            proc = self._run_smoke(
                artifacts_dir,
                extra_env={"VERIFY_SMOKE_FORCE_FAIL": "1"},
            )
            payload = self._assert_artifacts(artifacts_dir)
            self.assertEqual(payload.get("status"), "FAIL")
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
