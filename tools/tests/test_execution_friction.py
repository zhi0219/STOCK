import unittest

from tools.execution_friction import apply_friction


class ExecutionFrictionTests(unittest.TestCase):
    def test_rejection_handling(self) -> None:
        policy = {
            "fee_per_trade": 0.5,
            "fee_per_share": 0.0,
            "spread_bps": 0.0,
            "slippage_bps": 0.0,
            "latency_ms": 0.0,
            "partial_fill_prob": 0.0,
            "max_fill_fraction": 1.0,
            "reject_prob": 1.0,
            "fail_prob": 0.0,
            "gap_bps": 0.0,
            "gap_threshold_pct": 0.0,
        }
        result = apply_friction({"qty": 10, "price": 100, "side": "BUY"}, {"price": 100}, policy)
        self.assertEqual(result["fill_status"], "REJECTED")
        self.assertEqual(result["fill_qty"], 0.0)
        self.assertEqual(result["fee_usd"], 0.5)

    def test_failure_handling(self) -> None:
        policy = {
            "fee_per_trade": 0.25,
            "fee_per_share": 0.0,
            "spread_bps": 0.0,
            "slippage_bps": 0.0,
            "latency_ms": 0.0,
            "partial_fill_prob": 0.0,
            "max_fill_fraction": 1.0,
            "reject_prob": 0.0,
            "fail_prob": 1.0,
            "gap_bps": 0.0,
            "gap_threshold_pct": 0.0,
        }
        result = apply_friction({"qty": 5, "price": 50, "side": "SELL"}, {"price": 50}, policy)
        self.assertEqual(result["fill_status"], "FAILED")
        self.assertEqual(result["fill_qty"], 0.0)

    def test_gap_detection(self) -> None:
        policy = {
            "fee_per_trade": 0.0,
            "fee_per_share": 0.0,
            "spread_bps": 0.0,
            "slippage_bps": 0.0,
            "latency_ms": 0.0,
            "partial_fill_prob": 0.0,
            "max_fill_fraction": 1.0,
            "reject_prob": 0.0,
            "fail_prob": 0.0,
            "gap_bps": 10.0,
            "gap_threshold_pct": 0.5,
        }
        result = apply_friction(
            {"qty": 1, "price": 110, "side": "BUY"},
            {"price": 110, "prev_price": 100},
            policy,
        )
        self.assertEqual(result["gap_bps"], 10.0)
        self.assertGreater(result["gap_pct"], 0.0)

    def test_partial_fill(self) -> None:
        policy = {
            "fee_per_trade": 0.0,
            "fee_per_share": 0.0,
            "spread_bps": 0.0,
            "slippage_bps": 0.0,
            "latency_ms": 0.0,
            "partial_fill_prob": 1.0,
            "max_fill_fraction": 0.5,
            "reject_prob": 0.0,
            "fail_prob": 0.0,
            "gap_bps": 0.0,
            "gap_threshold_pct": 0.0,
        }
        result = apply_friction({"qty": 10, "price": 100, "side": "BUY"}, {"price": 100}, policy)
        self.assertEqual(result["fill_fraction"], 0.5)
        self.assertTrue(result["partial_fill"])


if __name__ == "__main__":
    unittest.main()
