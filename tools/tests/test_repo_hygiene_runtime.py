import unittest

from tools import repo_hygiene


class RepoHygieneRuntimeTests(unittest.TestCase):
    def test_runtime_path_classification(self) -> None:
        runtime_paths = [
            "Logs/events_2024-01-01.jsonl",
            "Logs/runtime/policy_registry.json",
            "artifacts/compile_check.log",
            "__pycache__/module.pyc",
        ]
        for path in runtime_paths:
            self.assertTrue(repo_hygiene.is_runtime_path(path))

        non_runtime_paths = [
            "Data/policy_registry.seed.json",
            "tools/safe_push_contract.py",
        ]
        for path in non_runtime_paths:
            self.assertFalse(repo_hygiene.is_runtime_path(path))


if __name__ == "__main__":
    unittest.main()
