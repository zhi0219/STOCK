import unittest

from tools.safe_push_utils import is_consistency_status_ok, parse_consistency_status


class SafePushUtilsTests(unittest.TestCase):
    def test_parse_consistency_status(self) -> None:
        log_text = """===BEGIN===\nCONSISTENCY_SUMMARY|status=DEGRADED|failed=0\n===END==="""
        self.assertEqual(parse_consistency_status(log_text), "DEGRADED")

    def test_accepts_pass_or_degraded(self) -> None:
        self.assertTrue(is_consistency_status_ok("PASS"))
        self.assertTrue(is_consistency_status_ok("DEGRADED"))
        self.assertFalse(is_consistency_status_ok("FAIL"))
        self.assertFalse(is_consistency_status_ok(None))


if __name__ == "__main__":
    unittest.main()
