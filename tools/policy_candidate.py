from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
DEFAULT_EVENTS = LOGS_DIR / "tournament_runs"
CANDIDATE_PATH = LOGS_DIR / "policy_candidate.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.policy_registry import WHITELIST_KEYS, get_policy, upsert_policy  # noqa: E402


class CandidateError(Exception):
    pass


def _find_latest_events() -> Path:
    if not DEFAULT_EVENTS.exists():
        raise CandidateError("No tournament runs found for proposals")
    candidates: List[Path] = []
    for path in DEFAULT_EVENTS.glob("*/events.jsonl"):
        candidates.append(path)
    if not candidates:
        raise CandidateError("No events.jsonl found under tournament runs")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _pick_proposal(path: Path) -> Dict[str, object]:
    proposal: Dict[str, object] | None = None
    if not path.exists():
        raise CandidateError(f"Events file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if str(event.get("event_type")) == "GUARD_PROPOSAL":
            proposal = event
    if not proposal:
        raise CandidateError(f"No GUARD_PROPOSAL found in {path}")
    return proposal


def _conservative_adjustments(base: Dict[str, object], reason: str) -> Dict[str, object]:
    adjusted: Dict[str, object] = dict(base)
    lower_reason = reason.lower()
    if "drawdown" in lower_reason:
        current = float(adjusted.get("max_drawdown", 0.05) or 0.05)
        adjusted["max_drawdown"] = max(0.005, round(current * 0.9, 4))
    if "stale" in lower_reason or "data" in lower_reason:
        current_gap = float(adjusted.get("min_interval_seconds", 30) or 30)
        adjusted["min_interval_seconds"] = min(120.0, current_gap + 5.0)
    current_rate = int(adjusted.get("max_orders_per_minute", 2) or 2)
    adjusted["max_orders_per_minute"] = max(1, current_rate - 1)
    return {k: v for k, v in adjusted.items() if k in WHITELIST_KEYS}


def _write_candidate(payload: Dict[str, object]) -> None:
    CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CANDIDATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(CANDIDATE_PATH)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate policy candidate from guard proposals")
    parser.add_argument("--events", help="Events file containing GUARD_PROPOSAL", default=None)
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        base_version, base_policy = get_policy()
        events_path = Path(args.events) if args.events else _find_latest_events()
        proposal = _pick_proposal(events_path)
        reason = str(proposal.get("proposal") or proposal.get("message") or "guard")
        adjusted_overrides = _conservative_adjustments(base_policy.get("risk_overrides", {}), reason)
        candidate_version = f"candidate-{time.strftime('%Y%m%d%H%M%S')}-v1"
        candidate_payload = {
            "policy_version": candidate_version,
            "based_on": base_version,
            "risk_overrides": adjusted_overrides,
            "proposal_reason": reason,
            "events_path": str(events_path),
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        upsert_policy(candidate_version, adjusted_overrides, based_on=base_version, source="candidate", evidence=str(events_path))
        _write_candidate(candidate_payload)
        print(f"Generated {candidate_version} with overrides {adjusted_overrides}")
    except CandidateError as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
