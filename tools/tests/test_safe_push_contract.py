import unittest
from pathlib import Path

from tools import safe_push_contract


class SafePushContractTests(unittest.TestCase):
    def test_safe_push_script_contract(self) -> None:
        script_path = Path("scripts") / "safe_push_v1.ps1"
        status, errors = safe_push_contract._check_contract(script_path)
        self.assertEqual(status, "PASS")
        self.assertEqual(errors, [])

    def test_safe_push_is_print_only(self) -> None:
        content = (Path("scripts") / "safe_push_v1.ps1").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertNotRegex(content, r"&\s*\$gitExe\s+push")
        self.assertNotRegex(content, r"\bRun-Git\b[^\r\n]*\bpush\b")
        self.assertIn("git push -u", content)


if __name__ == "__main__":
    unittest.main()
