from __future__ import annotations

import argparse
import csv
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from tools.promotion_gate_v2 import GateConfig
from tools.sim_tournament import BASELINE_CANDIDATES, run_strategy_tournament
from tools.replay_artifacts import build_decision_cards, write_replay_artifacts
from tools.strategy_pool import load_strategy_pool, select_candidates, write_strategy_pool_manifest
from tools.paths import to_repo_relative
from tools.experiment_ledger import DEFAULT_BASELINES, append_entry, build_entry
from tools.multiple_testing_control import TrialBudgetError, enforce_budget, write_enforcement_artifact

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
DEFAULT_QUOTES = ROOT / "Data" / "quotes.csv"
DEFAULT_MAX_STEPS = 180
DEFAULT_CANDIDATE_COUNT = 3
DEFAULT_MIN_STEPS = 60
DEFAULT_QUOTES_LIMIT = 240


@dataclass(frozen=True)
class PR28Config:
    runs_root: Path
    seed: int
    max_steps: int
    candidate_count: int
    min_steps: int
    quotes_limit: int


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit() -> str | None:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True)
            .strip()
        )
    except Exception:
        return None


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _atomic_copy_json(source: Path, dest: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    _atomic_write_json(dest, payload)


def _load_quotes(path: Path, limit: int | None = None) -> List[Dict[str, object]]:
    quotes: List[Dict[str, object]] = []
    if not path.exists():
        return quotes
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_price = row.get("price")
            if raw_price in (None, ""):
                continue
            try:
                price_val = float(raw_price)
            except ValueError:
                continue
            quotes.append({"price": price_val})
            if limit is not None and len(quotes) >= limit:
                break
    return quotes


def _synthetic_quotes(length: int) -> List[Dict[str, object]]:
    length = max(1, int(length))
    prices: List[Dict[str, object]] = []
    base = 100.0
    for idx in range(length):
        seasonal = (idx % 10) - 5
        price = base + idx * 0.05 + seasonal * 0.12
        prices.append({"price": round(price, 4)})
    return prices


def _ensure_pool_manifest(path: Path) -> Dict[str, object]:
    manifest = load_strategy_pool(path)
    if manifest:
        return manifest
    return write_strategy_pool_manifest(path)


def _baseline_entries(entries: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    return [entry for entry in entries if entry.get("is_baseline")]


def _candidate_entries(entries: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    return [entry for entry in entries if not entry.get("is_baseline")]


def _best_candidate(entries: Iterable[Dict[str, object]]) -> Dict[str, object] | None:
    ranked = sorted(entries, key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return ranked[0] if ranked else None


def _matchups(
    candidates: Iterable[Dict[str, object]],
    baselines: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    matchups: List[Dict[str, object]] = []
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id")
        candidate_score = float(candidate.get("score") or 0.0)
        for baseline in baselines:
            baseline_id = baseline.get("candidate_id")
            baseline_score = float(baseline.get("score") or 0.0)
            matchups.append(
                {
                    "candidate_id": candidate_id,
                    "baseline_id": baseline_id,
                    "candidate_score": candidate_score,
                    "baseline_score": baseline_score,
                    "score_delta": candidate_score - baseline_score,
                }
            )
    return matchups


def _judge_result(
    candidate: Dict[str, object] | None,
    baselines: List[Dict[str, object]],
    config: PR28Config,
    gate_config: GateConfig,
    base_fields: Dict[str, object],
) -> Dict[str, object]:
    reasons: List[str] = []
    insufficient = False
    if candidate is None:
        insufficient = True
        reasons.append("no_candidate_available")
    if len(baselines) < 2:
        insufficient = True
        reasons.append("missing_baselines")
    if config.max_steps < config.min_steps:
        insufficient = True
        reasons.append("insufficient_steps")

    candidate_score = float(candidate.get("score") or 0.0) if candidate else 0.0
    baseline_scores = {
        str(baseline.get("candidate_id") or "baseline"): float(baseline.get("score") or 0.0)
        for baseline in baselines
    }
    advantages = {bid: candidate_score - score for bid, score in baseline_scores.items()}

    status = "INSUFFICIENT_DATA" if insufficient else "PASS"
    min_advantage = 0.15
    stable_win = all(delta >= min_advantage for delta in advantages.values()) if advantages else False
    if not insufficient and not stable_win:
        status = "FAIL"
        reasons.append("advantage_below_threshold")

    return {
        **base_fields,
        "schema_version": 1,
        "status": status,
        "insufficient_data": bool(insufficient),
        "candidate_id": candidate.get("candidate_id") if candidate else None,
        "scores": {
            "candidate": candidate_score,
            "baselines": baseline_scores,
            "advantages": advantages,
        },
        "thresholds": {
            "min_advantage": min_advantage,
            "min_steps": config.min_steps,
            "max_drawdown_pct": gate_config.max_drawdown_pct,
        },
        "reasons": reasons,
    }


def _promotion_decision(
    judge_payload: Dict[str, object],
    candidate: Dict[str, object] | None,
    gate_config: GateConfig,
    base_fields: Dict[str, object],
) -> Dict[str, object]:
    reasons: List[str] = []
    thresholds = judge_payload.get("thresholds", {}) if isinstance(judge_payload, dict) else {}
    decision = "REJECT"
    promoted = False

    if judge_payload.get("status") != "PASS":
        reasons.append("judge_not_passed")
    if candidate is None:
        reasons.append("no_candidate_available")
    if candidate is not None:
        if not candidate.get("safety_pass", True):
            reasons.append("safety_constraints_failed")
        drawdown = float(candidate.get("metrics", {}).get("max_drawdown_pct") or 0.0)
        if drawdown > gate_config.max_drawdown_pct:
            reasons.append(f"drawdown>{gate_config.max_drawdown_pct:.2f}%")

    if not reasons:
        decision = "APPROVE"
        promoted = True
        reasons.append("risk_adjusted_outperformance")

    return {
        **base_fields,
        "schema_version": 1,
        "candidate_id": candidate.get("candidate_id") if candidate else None,
        "decision": decision,
        "promoted": promoted,
        "reasons": reasons,
        "thresholds": thresholds,
        "judge_status": judge_payload.get("status"),
        "scores": judge_payload.get("scores"),
    }


def _append_jsonl(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_pr28_flow(config: PR28Config) -> Dict[str, Path]:
    ts_utc = _now_ts()
    git_commit = _git_commit()
    safe_commit = git_commit if git_commit else "unknown"
    run_id = f"pr28_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = config.runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    latest_dir = config.runs_root / "_latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    enforcement = None
    try:
        enforcement = enforce_budget(
            requested_candidate_count=config.candidate_count,
            baseline_count=len(BASELINE_CANDIDATES),
        )
    except TrialBudgetError as exc:
        raise RuntimeError(str(exc)) from exc

    enforcement_path = ROOT / "artifacts" / f"multitest_enforcement_{run_id}.json"
    write_enforcement_artifact(enforcement_path, enforcement)
    if enforcement.status != "OK":
        print(
            "|".join(
                [
                    "MULTITEST_ENFORCEMENT",
                    f"status={enforcement.status}",
                    f"requested_candidate_count={enforcement.requested_candidate_count}",
                    f"enforced_candidate_count={enforcement.enforced_candidate_count}",
                    f"requested_trial_count={enforcement.requested_trial_count}",
                    f"enforced_trial_count={enforcement.enforced_trial_count}",
                    f"budget_candidate_count={enforcement.budget_candidate_count}",
                    f"budget_trial_count={enforcement.budget_trial_count}",
                    f"artifact={to_repo_relative(enforcement_path)}",
                    f"reasons={','.join(enforcement.reasons) if enforcement.reasons else 'none'}",
                ]
            )
        )

    pool_path = config.runs_root / "strategy_pool.json"
    pool = _ensure_pool_manifest(pool_path)
    candidates = select_candidates(pool, enforcement.enforced_candidate_count, config.seed)
    if not candidates and enforcement.enforced_candidate_count > 0:
        candidates = [
            {
                "candidate_id": "momentum_fallback",
                "family": "momentum",
                "params": {"lookback": 5, "threshold_pct": 0.5},
                "risk_profile_tags": ["risk_medium"],
                "guard_defaults": {},
            }
        ]

    quotes = _load_quotes(DEFAULT_QUOTES, limit=config.quotes_limit)
    data_source = "quotes.csv"
    if not quotes:
        quotes = _synthetic_quotes(config.quotes_limit)
        data_source = "synthetic_fallback"

    gate_config = GateConfig()
    tournament_payload = run_strategy_tournament(
        quotes,
        candidates,
        max_steps=config.max_steps,
        seed=config.seed,
        gate_config=gate_config,
    )
    entries = tournament_payload.get("entries", [])
    baseline_entries = _baseline_entries(entries)
    candidate_entries = _candidate_entries(entries)

    base_fields = {
        "ts_utc": ts_utc,
        "created_utc": ts_utc,
        "run_id": run_id,
        "git_commit": safe_commit,
    }

    tournament_path = run_dir / "tournament_result.json"
    judge_path = run_dir / "judge_result.json"
    promotion_path = run_dir / "promotion_decision.json"
    history_global = config.runs_root / "promotion_history.jsonl"
    history_run = run_dir / "promotion_history.jsonl"
    evidence_pack = {
        "run_dir": to_repo_relative(run_dir),
        "paths": {
            "tournament_result": to_repo_relative(tournament_path),
            "judge_result": to_repo_relative(judge_path),
            "promotion_decision": to_repo_relative(promotion_path),
            "promotion_history": to_repo_relative(history_global),
        },
    }

    tournament_result = {
        **base_fields,
        "schema_version": 1,
        "seed": config.seed,
        "max_steps": config.max_steps,
        "data_source": data_source,
        "candidates": [c.get("candidate_id") for c in candidates],
        "baselines": [b.get("candidate_id") for b in BASELINE_CANDIDATES],
        "entries": entries,
        "matchups": _matchups(candidate_entries, baseline_entries),
    }

    _atomic_write_json(tournament_path, tournament_result)
    _atomic_copy_json(tournament_path, latest_dir / "tournament_result_latest.json")

    best_candidate = _best_candidate(candidate_entries)
    judge_payload = _judge_result(best_candidate, baseline_entries, config, gate_config, base_fields)
    judge_payload["evidence_pack"] = evidence_pack
    _atomic_write_json(judge_path, judge_payload)
    _atomic_copy_json(judge_path, latest_dir / "judge_result_latest.json")

    promotion_payload = _promotion_decision(judge_payload, best_candidate, gate_config, base_fields)
    promotion_payload["evidence_pack"] = evidence_pack
    promotion_payload["baseline_results"] = baseline_entries
    promotion_payload["trial_count"] = len(entries)
    promotion_payload["candidate_count"] = len(candidate_entries)
    promotion_payload["search_scale_penalty"] = 0.0
    _atomic_write_json(promotion_path, promotion_payload)
    _atomic_copy_json(promotion_path, latest_dir / "promotion_decision_latest.json")

    history_event = {
        **base_fields,
        "schema_version": 1,
        "candidate_id": promotion_payload.get("candidate_id"),
        "decision": promotion_payload.get("decision"),
        "promoted": promotion_payload.get("promoted"),
        "reasons": promotion_payload.get("reasons"),
        "thresholds": promotion_payload.get("thresholds"),
        "evidence_pack": evidence_pack,
    }
    _append_jsonl(history_global, history_event)
    _append_jsonl(history_run, history_event)

    history_latest = {
        **base_fields,
        "schema_version": 1,
        "history_path": to_repo_relative(history_global),
        "last_event": history_event,
    }
    _atomic_write_json(latest_dir / "promotion_history_latest.json", history_latest)

    last_price = quotes[-1].get("price") if quotes else None
    data_health = "PASS" if data_source != "synthetic_fallback" else "ISSUE"
    decision_cards = build_decision_cards(
        tournament_payload=tournament_result,
        judge_payload=judge_payload,
        promotion_payload=promotion_payload,
        run_id=run_id,
        ts_utc=ts_utc,
        last_price=float(last_price) if last_price is not None else None,
        data_health=data_health,
        evidence_paths={
            "tournament_result": tournament_path,
            "judge_result": judge_path,
            "promotion_decision": promotion_path,
            "promotion_history": history_global,
        },
    )
    write_replay_artifacts(run_dir, run_id, safe_commit, decision_cards)

    ledger_window_config = {
        "max_steps": config.max_steps,
        "seed": config.seed,
        "candidate_count": len(candidate_entries),
        "quotes_limit": config.quotes_limit,
    }
    ledger_entry = build_entry(
        run_id=run_id,
        candidate_count=len(candidate_entries),
        trial_count=len(entries),
        baselines_used=DEFAULT_BASELINES,
        window_config=ledger_window_config,
        code_paths=[
            ROOT / "tools" / "pr28_training_loop.py",
            ROOT / "tools" / "sim_tournament.py",
            ROOT / "tools" / "promotion_gate_v2.py",
        ],
        requested_candidate_count=enforcement.requested_candidate_count,
        requested_trial_count=enforcement.requested_trial_count,
        enforced_candidate_count=enforcement.enforced_candidate_count,
        enforced_trial_count=enforcement.enforced_trial_count,
    )
    append_entry(ROOT / "artifacts", ledger_entry)

    return {
        "run_dir": run_dir,
        "tournament_result": tournament_path,
        "judge_result": judge_path,
        "promotion_decision": promotion_path,
        "promotion_history": history_global,
        "promotion_history_latest": latest_dir / "promotion_history_latest.json",
    }


def parse_args(argv: List[str]) -> object:
    parser = argparse.ArgumentParser(
        description="PR28 deterministic training loop (SIM-only)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--runs-root", default=str(RUNS_ROOT), help="Runs root under Logs/train_runs")
    parser.add_argument("--seed", type=int, default=28, help="Seed for deterministic selection")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="Max steps per candidate")
    parser.add_argument("--candidate-count", type=int, default=DEFAULT_CANDIDATE_COUNT)
    parser.add_argument("--min-steps", type=int, default=DEFAULT_MIN_STEPS)
    parser.add_argument("--quotes-limit", type=int, default=DEFAULT_QUOTES_LIMIT)
    parser.add_argument("--tiny", action="store_true", help="Use tiny fast mode")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = (ROOT / runs_root).resolve()
    if not str(runs_root).startswith(str(RUNS_ROOT.resolve())):
        raise ValueError("runs_root must be under Logs/train_runs")

    max_steps = int(args.max_steps)
    candidate_count = int(args.candidate_count)
    min_steps = int(args.min_steps)
    quotes_limit = int(args.quotes_limit)
    if args.tiny:
        max_steps = min(max_steps, 30)
        candidate_count = min(candidate_count, 2)
        min_steps = min(min_steps, 50)
        quotes_limit = min(quotes_limit, 80)

    config = PR28Config(
        runs_root=runs_root,
        seed=int(args.seed),
        max_steps=max_steps,
        candidate_count=candidate_count,
        min_steps=min_steps,
        quotes_limit=quotes_limit,
    )
    run_pr28_flow(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
