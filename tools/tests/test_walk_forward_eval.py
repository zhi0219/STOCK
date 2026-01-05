import unittest
from zoneinfo import ZoneInfo

from tools.paths import repo_root
from tools.walk_forward_eval import _load_bars, build_windows, evaluate_walk_forward


FIXTURE_PATH = repo_root() / "fixtures" / "walk_forward" / "ohlcv.csv"


class WalkForwardEvalTests(unittest.TestCase):
    def test_build_windows_respects_gap(self) -> None:
        bars = _load_bars(FIXTURE_PATH, ZoneInfo("America/New_York"))
        windows = build_windows(len(bars), train_size=5, gap_size=2, test_size=4, step_size=4)
        self.assertEqual(len(windows), 4)
        for window in windows:
            self.assertEqual(window.gap_end - window.gap_start, 2)

    def test_evaluate_includes_baselines(self) -> None:
        bars = _load_bars(FIXTURE_PATH, ZoneInfo("America/New_York"))
        windows = build_windows(len(bars), train_size=5, gap_size=2, test_size=4, step_size=4)
        report = evaluate_walk_forward(bars, windows, "placeholder")
        self.assertGreater(report["summary"]["window_count"], 0)
        self.assertIn("DoNothing", report["baselines"])
        self.assertIn("BuyHold", report["baselines"])
        self.assertIn("SimpleMomentum", report["baselines"])


if __name__ == "__main__":
    unittest.main()
