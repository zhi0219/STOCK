from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from tools.paths import logs_dir
from tools.promotion_gate_v2 import GateConfig, evaluate_safety
from tools.sim_tournament import BASELINE_CANDIDATES, run_strategy_tournament

ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = logs_dir() / "train_runs"
ARTIFACTS_DIR = ROOT / "artifacts"


@dataclass(frozen=True)
class WalkForwardConfig:
    runs_root: Path
    windows: int
    train_size: int
    eval_size: int
    seed: int
    max_steps: int
    candidate_count: int
    min_pass_rate: float
    min_baseline_beats: int
    min_windows_required: int
    artifacts_dir: Path | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _write_jsonl(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _synthetic_quotes(length: int) -> List[Dict[str, object]]:
    length = max(1, int(length))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    quotes: List[Dict[str, object]] = []
    base = 100.0
    for idx in range(length):
        seasonal = (idx % 12) - 6
        price = base + idx * 0.08 + seasonal * 0.04
        quotes.append(
            {
                "ts_utc": (start + timedelta(minutes=idx)).isoformat(),
                "price": round(price, 4),
                "symbol": "SIM",
                "source": "synthetic",
            }
        )
    return quotes


def _default_candidates() -> List[Dict[str, object]]:
    return [
        {
            "candidate_id": "wf_momentum",
            "family": "momentum",
            "params": {"lookback": 5, "threshold_pct": 0.25},
            "risk_profile_tags": ["risk_medium"],
            "guard_defaults": {"max_drawdown_pct": 6.0, "max_turnover": 18},
        },
        {
            "candidate_id": "wf_mean_reversion",
            "family": "mean_reversion",
            "params": {"window": 10, "zscore": 1.0},
            "risk_profile_tags": ["risk_low"],
            "guard_defaults": {"max_drawdown_pct": 4.0, "max_turnover": 12},
        },
    ]


def _select_candidates(candidates: Sequence[Dict[str, object]], count: int, seed: int) -> List[Dict[str, object]]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: str(item.get("candidate_id", "")))
    count = max(1, min(int(count), len(ordered)))
    offset = int(seed) % len(ordered)
    return [ordered[(offset + idx) % len(ordered)] for idx in range(count)]


def _best_candidate(entries: Iterable[Dict[str, object]]) -> Dict[str, object] | None:
    candidates = [entry for entry in entries if not entry.get("is_baseline")]
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("score") or 0.0))


def _extract_eval_metrics(entries: Iterable[Dict[str, object]], candidate_id: str) -> Dict[str, object]:
    baseline_scores: Dict[str, float] = {}
    candidate_score = 0.0
    candidate_metrics: Dict[str, object] = {}
    candidate_safety = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("candidate_id") or "")
        score = float(entry.get("score") or 0.0)
        if entry.get("is_baseline"):
            baseline_scores[cid] = score
        elif cid == candidate_id:
            candidate_score = score
            candidate_metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
            candidate_safety = bool(entry.get("safety_pass"))
    return {
        "candidate_score": candidate_score,
        "candidate_metrics": candidate_metrics,
        "candidate_safety": candidate_safety,
        "baseline_scores": baseline_scores,
    }


def run_walk_forward(config: WalkForwardConfig) -> Dict[str, Path]:
    run_id = f"walk_forward_{_now_ts()}"
    run_dir = config.runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    latest_dir = config.runs_root / "_latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    total_points = config.windows * (config.train_size + config.eval_size)
    quotes = _synthetic_quotes(total_points)

    windows_payload: List[Dict[str, object]] = []
    passes = 0
    gate_config = GateConfig()

    for idx in range(config.windows):
        train_start = idx * (config.train_size + config.eval_size)
        train_end = train_start + config.train_size - 1
        eval_start = train_end + 1
        eval_end = eval_start + config.eval_size - 1

        train_quotes = quotes[train_start : train_end + 1]
        eval_quotes = quotes[eval_start : eval_end + 1]

        candidates = _select_candidates(_default_candidates(), config.candidate_count, config.seed + idx)
        if not candidates:
            candidates = _default_candidates()[:1]

        train_result = run_strategy_tournament(train_quotes, candidates, max_steps=config.max_steps, seed=config.seed)
        entries = train_result.get("entries", []) if isinstance(train_result, dict) else []
        best_candidate = _best_candidate(entries) or candidates[0]
        best_candidate_id = str(best_candidate.get("candidate_id") or "unknown")

        eval_result = run_strategy_tournament(eval_quotes, [best_candidate], max_steps=config.max_steps, seed=config.seed)
        eval_entries = eval_result.get("entries", []) if isinstance(eval_result, dict) else []
        eval_metrics = _extract_eval_metrics(eval_entries, best_candidate_id)

        baseline_scores = eval_metrics.get("baseline_scores", {})
        baseline_beats = [
            bid for bid, score in baseline_scores.items() if eval_metrics.get("candidate_score", 0.0) > score
        ]
        safety_pass, safety_failures = evaluate_safety(
            eval_metrics.get("candidate_metrics", {}), gate_config
        )
        min_baseline_beats = max(1, config.min_baseline_beats)
        window_pass = bool(
            safety_pass
            and eval_metrics.get("candidate_safety", False)
            and len(baseline_beats) >= min_baseline_beats
        )
        if window_pass:
            passes += 1

        windows_payload.append(
            {
                "ts_utc": _now_iso(),
                "window_id": idx + 1,
                "train_start_index": train_start,
                "train_end_index": train_end,
                "eval_start_index": eval_start,
                "eval_end_index": eval_end,
                "train_start_ts": quotes[train_start]["ts_utc"],
                "train_end_ts": quotes[train_end]["ts_utc"],
                "eval_start_ts": quotes[eval_start]["ts_utc"],
                "eval_end_ts": quotes[eval_end]["ts_utc"],
                "candidate_id": best_candidate_id,
                "candidate_score": eval_metrics.get("candidate_score"),
                "baseline_scores": baseline_scores,
                "baseline_beats": baseline_beats,
                "baseline_beats_required": min_baseline_beats,
                "pass": window_pass,
                "safety_failures": safety_failures,
                "metrics": {
                    "candidate": eval_metrics.get("candidate_metrics"),
                },
            }
        )

    pass_rate = passes / max(1, config.windows)
    min_windows_required = max(1, config.min_windows_required)
    status = "PASS"
    if config.windows < min_windows_required:
        status = "INSUFFICIENT_DATA"
    elif pass_rate < config.min_pass_rate:
        status = "FAIL"

    overall = {
        "schema_version": 1,
        "ts_utc": _now_iso(),
        "run_id": run_id,
        "seed": config.seed,
        "data_source": "synthetic",
        "window_count": config.windows,
        "train_size": config.train_size,
        "eval_size": config.eval_size,
        "max_steps": config.max_steps,
        "candidate_count": config.candidate_count,
        "min_pass_rate": config.min_pass_rate,
        "min_baseline_beats": config.min_baseline_beats,
        "min_windows_required": min_windows_required,
        "pass_count": passes,
        "pass_rate": round(pass_rate, 4),
        "status": status,
        "baselines": [b.get("candidate_id") for b in BASELINE_CANDIDATES],
    }

    result_path = run_dir / "walk_forward_result.json"
    windows_path = run_dir / "walk_forward_windows.jsonl"
    _atomic_write_json(result_path, overall)
    _write_jsonl(windows_path, windows_payload)

    _atomic_write_json(latest_dir / "walk_forward_result_latest.json", overall)
    (latest_dir / "walk_forward_windows_latest.jsonl").write_text(
        windows_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    if config.artifacts_dir:
        config.artifacts_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(config.artifacts_dir / "walk_forward_result.json", overall)
        (config.artifacts_dir / "walk_forward_windows.jsonl").write_text(
            windows_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

    return {
        "run_dir": run_dir,
        "walk_forward_result": result_path,
        "walk_forward_windows": windows_path,
        "latest_result": latest_dir / "walk_forward_result_latest.json",
        "latest_windows": latest_dir / "walk_forward_windows_latest.jsonl",
    }


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic walk-forward evaluator (SIM-only)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--runs-root", default=str(RUNS_ROOT))
    parser.add_argument("--windows", type=int, default=3)
    parser.add_argument("--train-size", type=int, default=40)
    parser.add_argument("--eval-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=32)
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--candidate-count", type=int, default=2)
    parser.add_argument("--min-pass-rate", type=float, default=0.5)
    parser.add_argument("--min-baseline-beats", type=int, default=1)
    parser.add_argument("--min-windows-required", type=int, default=2)
    parser.add_argument("--no-artifacts", action="store_true")
    parser.add_argument("--tiny", action="store_true")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = (ROOT / runs_root).resolve()
    if not str(runs_root).startswith(str(RUNS_ROOT.resolve())):
        raise ValueError("runs_root must be under Logs/train_runs")

    windows = int(args.windows)
    train_size = int(args.train_size)
    eval_size = int(args.eval_size)
    max_steps = int(args.max_steps)
    candidate_count = int(args.candidate_count)
    min_pass_rate = float(args.min_pass_rate)
    min_baseline_beats = int(args.min_baseline_beats)
    min_windows_required = int(args.min_windows_required)

    if args.tiny:
        windows = min(windows, 2)
        train_size = min(train_size, 20)
        eval_size = min(eval_size, 10)
        max_steps = min(max_steps, 20)
        candidate_count = min(candidate_count, 1)
        min_windows_required = min(min_windows_required, windows)

    artifacts_dir = None if args.no_artifacts else ARTIFACTS_DIR

    config = WalkForwardConfig(
        runs_root=runs_root,
        windows=windows,
        train_size=train_size,
        eval_size=eval_size,
        seed=int(args.seed),
        max_steps=max_steps,
        candidate_count=candidate_count,
        min_pass_rate=min_pass_rate,
        min_baseline_beats=min_baseline_beats,
        min_windows_required=min_windows_required,
        artifacts_dir=artifacts_dir,
    )
    run_walk_forward(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
