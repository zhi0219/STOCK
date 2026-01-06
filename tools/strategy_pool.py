from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = ROOT / "Logs" / "train_runs" / "strategy_pool.json"


@dataclass(frozen=True)
class StrategyCandidate:
    candidate_id: str
    family: str
    params: Dict[str, object]
    risk_profile_tags: List[str]
    guard_defaults: Dict[str, object]

    def as_dict(self) -> Dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "family": self.family,
            "params": self.params,
            "risk_profile_tags": list(self.risk_profile_tags),
            "guard_defaults": dict(self.guard_defaults),
        }


def _stable_hash(payload: Dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:12]


def _candidate_id(family: str, params: Dict[str, object]) -> str:
    return f"{family}_{_stable_hash({'family': family, 'params': params})}"


def _risk_profile(family: str, params: Dict[str, object]) -> List[str]:
    if family in {"mean_reversion", "ma_crossover"}:
        return ["risk_low", "stable"]
    if family == "momentum":
        return ["risk_medium", "trend"]
    if family == "breakout":
        return ["risk_medium", "breakout"]
    return ["risk_unknown"]


def _guard_defaults(family: str, params: Dict[str, object]) -> Dict[str, object]:
    if family == "momentum":
        return {"max_drawdown_pct": 6.0, "max_turnover": 20}
    if family == "breakout":
        return {"max_drawdown_pct": 7.0, "max_turnover": 25}
    if family == "mean_reversion":
        return {"max_drawdown_pct": 4.0, "max_turnover": 15}
    if family == "ma_crossover":
        return {"max_drawdown_pct": 5.0, "max_turnover": 12}
    return {"max_drawdown_pct": 5.0, "max_turnover": 10}


def _expand_families() -> Iterable[StrategyCandidate]:
    momentum_lookbacks = [5, 10, 20]
    momentum_thresholds = [0.3, 0.6]
    for lookback in momentum_lookbacks:
        for threshold_pct in momentum_thresholds:
            params = {"lookback": lookback, "threshold_pct": threshold_pct}
            family = "momentum"
            yield StrategyCandidate(
                candidate_id=_candidate_id(family, params),
                family=family,
                params=params,
                risk_profile_tags=_risk_profile(family, params),
                guard_defaults=_guard_defaults(family, params),
            )

    ma_fast = [5, 10]
    ma_slow = [20, 50]
    for fast in ma_fast:
        for slow in ma_slow:
            if fast >= slow:
                continue
            params = {"fast": fast, "slow": slow}
            family = "ma_crossover"
            yield StrategyCandidate(
                candidate_id=_candidate_id(family, params),
                family=family,
                params=params,
                risk_profile_tags=_risk_profile(family, params),
                guard_defaults=_guard_defaults(family, params),
            )

    mean_windows = [10, 20]
    mean_thresholds = [1.0, 1.5]
    for window in mean_windows:
        for zscore in mean_thresholds:
            params = {"window": window, "zscore": zscore}
            family = "mean_reversion"
            yield StrategyCandidate(
                candidate_id=_candidate_id(family, params),
                family=family,
                params=params,
                risk_profile_tags=_risk_profile(family, params),
                guard_defaults=_guard_defaults(family, params),
            )

    breakout_windows = [10, 20]
    for window in breakout_windows:
        params = {"window": window}
        family = "breakout"
        yield StrategyCandidate(
            candidate_id=_candidate_id(family, params),
            family=family,
            params=params,
            risk_profile_tags=_risk_profile(family, params),
            guard_defaults=_guard_defaults(family, params),
        )


def build_strategy_pool() -> Dict[str, object]:
    candidates = list(_expand_families())
    families: Dict[str, int] = {}
    for candidate in candidates:
        families[candidate.family] = families.get(candidate.family, 0) + 1
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "families": families,
        "candidates": [candidate.as_dict() for candidate in candidates],
    }


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_strategy_pool_manifest(path: Path | None = None) -> Dict[str, object]:
    manifest_path = path or DEFAULT_MANIFEST_PATH
    pool = build_strategy_pool()
    _atomic_write_json(manifest_path, pool)
    families = pool.get("families", {})
    family_names = ",".join(sorted(families.keys())) if isinstance(families, dict) else "unknown"
    count = len(pool.get("candidates", [])) if isinstance(pool.get("candidates"), list) else 0
    print(f"STRATEGY_POOL_SUMMARY|count={count}|families={family_names}")
    return pool


def select_candidates(pool: Dict[str, object], count: int, seed: int) -> List[Dict[str, object]]:
    candidates = pool.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    ordered = sorted(
        [c for c in candidates if isinstance(c, dict)],
        key=lambda item: str(item.get("candidate_id", "")),
    )
    if not ordered:
        return []
    count = max(0, min(int(count), len(ordered)))
    if count == 0:
        return []
    offset = int(seed) % len(ordered)
    selected: List[Dict[str, object]] = []
    for idx in range(count):
        selected.append(ordered[(offset + idx) % len(ordered)])
    return selected


def load_strategy_pool(path: Path | None = None) -> Dict[str, object]:
    manifest_path = path or DEFAULT_MANIFEST_PATH
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_args(argv: List[str]) -> object:
    import argparse

    parser = argparse.ArgumentParser(
        description="Build deterministic strategy pool manifest",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Manifest output path",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    path = Path(args.output)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    write_strategy_pool_manifest(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
