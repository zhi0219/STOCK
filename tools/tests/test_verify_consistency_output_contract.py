import unittest

from tools import verify_consistency


class VerifyConsistencyOutputContractTests(unittest.TestCase):
    def test_pass_marker_has_no_next_step(self) -> None:
        lines = verify_consistency._consistency_status_lines(
            "PASS",
            skipped_checks=[],
            how_to_opt_in=verify_consistency.CONSISTENCY_OPT_IN_FLAGS,
            next_step_cmd=verify_consistency.CONSISTENCY_NEXT_STEP_CMD,
        )
        joined = "\n".join(lines)
        self.assertIn("CONSISTENCY_OK|status=PASS", joined)
        self.assertNotIn("Next step:", joined)

    def test_degraded_marker_has_no_next_step(self) -> None:
        lines = verify_consistency._consistency_status_lines(
            "DEGRADED",
            skipped_checks=["events archives", "verify_pr20_gate.py (legacy)"],
            how_to_opt_in=verify_consistency.CONSISTENCY_OPT_IN_FLAGS,
            next_step_cmd=verify_consistency.CONSISTENCY_NEXT_STEP_CMD,
        )
        joined = "\n".join(lines)
        self.assertIn("CONSISTENCY_OK_BUT_DEGRADED", joined)
        self.assertIn("skipped=events archives,verify_pr20_gate.py (legacy)", joined)
        self.assertNotIn("Next step:", joined)

    def test_default_path_passes_without_optional_skips(self) -> None:
        results = [
            verify_consistency.CheckResult("events schema", True),
            verify_consistency.CheckResult("status.json", True),
        ]
        status, summary_line, skipped_checks, degraded_reasons, has_failures = (
            verify_consistency._summarize_results(results, [], False)
        )
        self.assertEqual(status, "PASS")
        self.assertEqual(summary_line, "PASS: consistency checks succeeded")
        self.assertEqual(skipped_checks, [])
        self.assertEqual(degraded_reasons, [])
        self.assertFalse(has_failures)

    def test_fail_marker_has_next_and_nonzero_exit(self) -> None:
        lines = verify_consistency._consistency_status_lines(
            "FAIL",
            skipped_checks=[],
            how_to_opt_in=verify_consistency.CONSISTENCY_OPT_IN_FLAGS,
            next_step_cmd=verify_consistency.CONSISTENCY_NEXT_STEP_CMD,
        )
        joined = "\n".join(lines)
        self.assertIn("CONSISTENCY_FAIL|next=python tools/verify_consistency.py", joined)
        self.assertNotIn("Next step:", joined)
        self.assertEqual(verify_consistency._exit_code(has_failures=True), 1)


if __name__ == "__main__":
    unittest.main()
