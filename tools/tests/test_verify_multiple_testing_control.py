import json
import tempfile
import unittest
from pathlib import Path

from tools import verify_multiple_testing_control


class VerifyMultipleTestingControlTests(unittest.TestCase):
    def _write_ledger(self, artifacts_dir: Path, entry: dict) -> None:
        ledger_path = artifacts_dir / "experiment_ledger.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    def _write_budget(self, tmp_dir: Path, trial_count: int, candidate_count: int) -> Path:
        budget_path = tmp_dir / "trial_budget.json"
        payload = {"trial_count": trial_count, "candidate_count": candidate_count}
        budget_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return budget_path

    def _run_gate(self, artifacts_dir: Path, budget_path: Path) -> tuple[int, dict]:
        rc = verify_multiple_testing_control.main(
            [
                "--artifacts-dir",
                str(artifacts_dir),
                "--budget-path",
                str(budget_path),
            ]
        )
        report = json.loads(
            (artifacts_dir / "experiment_ledger_summary.json").read_text(encoding="utf-8")
        )
        return rc, report

    def test_missing_ledger_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            artifacts_dir = tmp_path / "artifacts"
            budget_path = self._write_budget(tmp_path, trial_count=5, candidate_count=2)
            rc, report = self._run_gate(artifacts_dir, budget_path)
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("ledger_missing", report["reasons"])

    def test_exceeded_trial_budget_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            artifacts_dir = tmp_path / "artifacts"
            budget_path = self._write_budget(tmp_path, trial_count=2, candidate_count=2)
            entry = {
                "run_id": "run_1",
                "timestamp": "2024-01-01T00:00:00Z",
                "candidate_count": 2,
                "trial_count": 3,
                "baselines_used": ["DoNothing", "Buy&Hold", "SimpleMomentum"],
                "window_config_hash": "hash",
                "code_hash": "hash",
            }
            self._write_ledger(artifacts_dir, entry)
            rc, report = self._run_gate(artifacts_dir, budget_path)
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("trial_budget_exceeded", report["reasons"])

    def test_exceeded_candidate_budget_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            artifacts_dir = tmp_path / "artifacts"
            budget_path = self._write_budget(tmp_path, trial_count=6, candidate_count=1)
            entry = {
                "run_id": "run_candidate",
                "timestamp": "2024-01-01T00:00:00Z",
                "candidate_count": 2,
                "trial_count": 3,
                "baselines_used": ["DoNothing", "Buy&Hold", "SimpleMomentum"],
                "window_config_hash": "hash",
                "code_hash": "hash",
            }
            self._write_ledger(artifacts_dir, entry)
            rc, report = self._run_gate(artifacts_dir, budget_path)
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("candidate_budget_exceeded", report["reasons"])

    def test_missing_baselines_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            artifacts_dir = tmp_path / "artifacts"
            budget_path = self._write_budget(tmp_path, trial_count=5, candidate_count=2)
            entry = {
                "run_id": "run_2",
                "timestamp": "2024-01-01T00:00:00Z",
                "candidate_count": 2,
                "trial_count": 2,
                "baselines_used": ["DoNothing"],
                "window_config_hash": "hash",
                "code_hash": "hash",
            }
            self._write_ledger(artifacts_dir, entry)
            rc, report = self._run_gate(artifacts_dir, budget_path)
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["status"], "FAIL")
        reasons = ",".join(report["reasons"])
        self.assertIn("missing_baselines", reasons)

    def test_normal_case_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            artifacts_dir = tmp_path / "artifacts"
            budget_path = self._write_budget(tmp_path, trial_count=5, candidate_count=2)
            entry = {
                "run_id": "run_3",
                "timestamp": "2024-01-01T00:00:00Z",
                "candidate_count": 2,
                "trial_count": 2,
                "requested_candidate_count": 3,
                "requested_trial_count": 5,
                "enforced_candidate_count": 2,
                "enforced_trial_count": 2,
                "baselines_used": ["DoNothing", "Buy&Hold", "SimpleMomentum"],
                "window_config_hash": "hash",
                "code_hash": "hash",
            }
            self._write_ledger(artifacts_dir, entry)
            rc, report = self._run_gate(artifacts_dir, budget_path)
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
