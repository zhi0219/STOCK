from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs" / "train_runs"
SUMMARY_TAG = "PR16_GATE_SUMMARY"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.promotion_gate_v2 import GateConfig, evaluate_promotion_gate
from tools.sim_tournament import BASELINE_CANDIDATES, run_strategy_tournament
from tools.strategy_pool import select_candidates, write_strategy_pool_manifest


@dataclass(frozen=True)
class GateRun:
    run_id: str
    seed: int
    quote_trend: float


def _synthesize_quotes(count: int, trend: float) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    for idx in range(count):
        price += trend + ((idx % 7) - 3) * 0.2
        rows.append({"ts_utc": ts.isoformat(), "price": round(price, 2)})
        ts += timedelta(minutes=5)
    return rows


def _write_candidates(run_dir: Path, pool: Dict[str, object], seed: int) -> List[Dict[str, object]]:
    selected = select_candidates(pool, count=4, seed=seed)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_dir.name,
        "seed": seed,
        "pool_manifest": str(LOGS_DIR / "strategy_pool.json"),
        "baselines": BASELINE_CANDIDATES,
        "candidates": selected,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return selected


def _flatten_entry(entry: Dict[str, object]) -> Dict[str, object]:
    metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
    flat = {
        "candidate_id": entry.get("candidate_id"),
        "score": entry.get("score"),
        "safety_pass": entry.get("safety_pass"),
    }
    flat.update(metrics)
    return flat


def _write_tournament(
    run_dir: Path, quotes: List[Dict[str, object]], candidates: List[Dict[str, object]], seed: int
) -> Tuple[Dict[str, object], List[Dict[str, object]], List[Dict[str, object]]]:
    payload = run_strategy_tournament(quotes, candidates, max_steps=120, seed=seed, gate_config=GateConfig())
    (run_dir / "tournament.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    entries = payload.get("entries", [])
    entries = entries if isinstance(entries, list) else []
    baselines = [_flatten_entry(entry) for entry in entries if entry.get("is_baseline")]
    candidates_flat = [_flatten_entry(entry) for entry in entries if not entry.get("is_baseline")]
    return payload, baselines, candidates_flat


def _write_promotion_artifacts(
    run_dir: Path, baselines: List[Dict[str, object]], candidates: List[Dict[str, object]]
) -> Dict[str, object]:
    best_candidate = max(candidates, key=lambda item: item.get("score", -1e9), default=None)
    if best_candidate and not best_candidate.get("safety_pass"):
        best_candidate = None
    recommendation = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_dir.name,
        "candidate_id": best_candidate.get("candidate_id") if best_candidate else None,
        "recommendation": "APPROVE" if best_candidate else "REJECT",
        "reasons": ["top_scoring_candidate"] if best_candidate else ["no_safe_candidate_available"],
        "metrics": best_candidate or {},
    }
    (run_dir / "promotion_recommendation.json").write_text(
        json.dumps(recommendation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stress_report = {
        "schema_version": 1,
        "status": "PASS",
        "baseline_pass": True,
        "stress_pass": True,
        "fail_reasons": [],
        "scenarios": [
            {"scenario": "BASELINE", "pass": True},
            {"scenario": "STRESS_A", "pass": True},
        ],
    }
    decision = evaluate_promotion_gate(
        best_candidate,
        baselines,
        run_dir.name,
        GateConfig(),
        stress_report=stress_report,
    )
    (run_dir / "promotion_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return decision


def _schema_ok(payload: Dict[str, object], keys: List[str]) -> bool:
    return all(key in payload for key in keys)


def _negative_tests() -> List[str]:
    failures: List[str] = []
    baselines = [{"candidate_id": "baseline_do_nothing", "score": 10.0}]
    candidate_low = {"candidate_id": "candidate_low", "score": -5.0, "max_drawdown_pct": 1.0, "turnover": 0, "reject_rate": 0.0}
    decision_low = evaluate_promotion_gate(
        candidate_low,
        baselines,
        "neg_low",
        GateConfig(),
        stress_report={"status": "PASS", "baseline_pass": True, "stress_pass": True, "scenarios": [{"pass": True}]},
    )
    if decision_low.get("decision") != "REJECT":
        failures.append("baseline_win_reject_missing")

    candidate_risky = {"candidate_id": "candidate_risky", "score": 50.0, "max_drawdown_pct": 50.0, "turnover": 2, "reject_rate": 0.0}
    decision_risky = evaluate_promotion_gate(
        candidate_risky,
        baselines,
        "neg_risk",
        GateConfig(),
        stress_report={"status": "PASS", "baseline_pass": True, "stress_pass": True, "scenarios": [{"pass": True}]},
    )
    if decision_risky.get("decision") != "REJECT":
        failures.append("risk_reject_missing")
    return failures


def _summary_line(status: str, degraded: bool, reasons: List[str]) -> str:
    detail = ",".join(reasons) if reasons else "none"
    return "|".join([SUMMARY_TAG, f"status={status}", f"degraded={int(degraded)}", f"reasons={detail}"])


def _run_ui_time_math() -> tuple[bool, str]:
    script = ROOT / "tools" / "verify_ui_time_math.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    ok = result.returncode == 0 and "UI_TIME_MATH_SUMMARY|status=PASS" in output
    return ok, output.strip()


def main() -> int:
    status = "PASS"
    reasons: List[str] = []
    degraded = False

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    pool = write_strategy_pool_manifest(LOGS_DIR / "strategy_pool.json")
    if not _schema_ok(pool, ["schema_version", "generated_at", "families", "candidates"]):
        status = "FAIL"
        reasons.append("strategy_pool_schema_invalid")

    runs = [
        GateRun(run_id="pr16_gate_run_a", seed=101, quote_trend=0.2),
        GateRun(run_id="pr16_gate_run_b", seed=202, quote_trend=-0.1),
    ]

    for run in runs:
        run_dir = LOGS_DIR / "pr16_gate" / run.run_id
        quotes = _synthesize_quotes(120, run.quote_trend)
        candidates = _write_candidates(run_dir, pool, run.seed)
        _, baselines, candidate_entries = _write_tournament(run_dir, quotes, candidates, run.seed)
        decision = _write_promotion_artifacts(run_dir, baselines, candidate_entries)
        if not _schema_ok(decision, ["candidate_id", "decision", "reasons", "required_next_steps"]):
            status = "FAIL"
            reasons.append(f"promotion_decision_schema_invalid:{run.run_id}")

    for run in runs:
        run_dir = LOGS_DIR / "pr16_gate" / run.run_id
        for name in ["candidates.json", "tournament.json", "promotion_recommendation.json", "promotion_decision.json"]:
            if not (run_dir / name).exists():
                status = "FAIL"
                reasons.append(f"missing_{name}:{run.run_id}")

    for failure in _negative_tests():
        status = "FAIL"
        reasons.append(failure)

    ui_ok, ui_output = _run_ui_time_math()
    if not ui_ok:
        status = "FAIL"
        reasons.append("ui_time_math_failed")

    summary = _summary_line(status, degraded, reasons)
    print("PR16_GATE_START")
    print(summary)
    if ui_output:
        print("UI_TIME_MATH_OUTPUT_START")
        print(ui_output)
        print("UI_TIME_MATH_OUTPUT_END")
    print(summary)
    print("PR16_GATE_END")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
