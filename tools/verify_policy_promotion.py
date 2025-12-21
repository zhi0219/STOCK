from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
TOOLS_DIR = ROOT / "tools"

PYTHON_BIN = Path(".\\.venv\\Scripts\\python.exe") if Path(".\\.venv\\Scripts\\python.exe").exists() else Path(sys.executable)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.policy_registry import get_policy, promote_policy, reject_policy  # noqa: E402


class PromotionError(Exception):
    pass


def _run_tournament(args: List[str]) -> Path:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise PromotionError(result.stdout + "\n" + result.stderr)
    summary_candidates = sorted(LOGS_DIR.glob("tournament_runs/tournament_summary_*.json"))
    if not summary_candidates:
        raise PromotionError("Tournament summary missing")
    return summary_candidates[-1]


def _aggregate(runs: List[Dict[str, object]]) -> Dict[str, float]:
    equity = [float(run.get("final_equity_usd", 0.0)) for run in runs]
    drawdowns = [float(run.get("max_drawdown_pct", 0.0)) for run in runs]
    postmortems = [int(run.get("num_postmortems", 0)) for run in runs]
    return {
        "avg_equity": sum(equity) / len(equity) if equity else 0.0,
        "worst_drawdown": max(drawdowns) if drawdowns else 0.0,
        "postmortems": sum(postmortems),
    }


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify policy promotion between baseline and candidate")
    parser.add_argument("--input", default=str(ROOT / "Data" / "quotes.csv"))
    parser.add_argument("--windows", required=True, help="Tournament windows")
    parser.add_argument("--variants", default="baseline")
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--candidate", default=str(LOGS_DIR / "policy_candidate.json"))
    parser.add_argument("--equity-threshold", type=float, default=0.95)
    return parser.parse_args(argv)


def _load_candidate(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise PromotionError(f"Candidate file missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PromotionError(f"Invalid candidate json: {exc}") from exc


def _filter_runs(summary_path: Path, policy_version: str) -> List[Dict[str, object]]:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return [run for run in payload.get("runs", []) if str(run.get("policy_version")) == policy_version]


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    candidate_payload = _load_candidate(Path(args.candidate))
    candidate_version = str(candidate_payload.get("policy_version"))
    baseline_version, _ = get_policy()

    common_cmd = [
        str(PYTHON_BIN),
        str(TOOLS_DIR / "sim_tournament.py"),
        "--input",
        str(args.input),
        "--windows",
        str(args.windows),
        "--variants",
        str(args.variants),
        "--max-steps",
        str(args.max_steps),
    ]

    baseline_summary = _run_tournament(common_cmd + ["--policy-version", baseline_version])
    candidate_summary = _run_tournament(common_cmd + ["--policy-version", candidate_version])

    baseline_runs = _filter_runs(baseline_summary, baseline_version)
    candidate_runs = _filter_runs(candidate_summary, candidate_version)
    if not baseline_runs or not candidate_runs:
        raise PromotionError("Missing tournament runs for comparison")

    base_metrics = _aggregate(baseline_runs)
    cand_metrics = _aggregate(candidate_runs)

    if cand_metrics["worst_drawdown"] > base_metrics["worst_drawdown"]:
        reject_policy(candidate_version, evidence=str(candidate_summary))
        print("POLICY_REJECTED: drawdown worse")
        return 1
    if cand_metrics["postmortems"] > base_metrics["postmortems"]:
        reject_policy(candidate_version, evidence=str(candidate_summary))
        print("POLICY_REJECTED: more postmortems")
        return 1
    if cand_metrics["avg_equity"] < base_metrics["avg_equity"] * float(args.equity_threshold):
        reject_policy(candidate_version, evidence=str(candidate_summary))
        print("POLICY_REJECTED: equity shortfall")
        return 1

    promote_policy(candidate_version, evidence=str(candidate_summary))
    print(f"POLICY_PROMOTED {candidate_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
