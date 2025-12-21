from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from tools.policy_registry import get_policy
LOGS_DIR = ROOT / "Logs"
REPORTS_ROOT = ROOT / "Reports" / "judge"
DEFAULT_QUOTES = ROOT / "Data" / "quotes.csv"
DEFAULT_CONFIG = ROOT / "Config" / "judge_set.yaml"
DEFAULT_STATE = LOGS_DIR / "train_service" / "judge_state.json"
DEFAULT_RUNS_ROOT = LOGS_DIR / "train_runs"

BASELINES = ("DoNothing", "BuyHold")
INITIAL_CASH = 10_000.0
DRAWNDOWN_LIMIT = 0.12
TURNOVER_LIMIT = 250
MARGIN_BUFFER = 5.0


class JudgeError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _iter_quotes(path: Path) -> Iterable[Dict[str, object]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            record: Dict[str, object] = {k: v for k, v in row.items() if v not in {None, ""}}
            try:
                record["price"] = float(record.get("price") or 0.0)
            except Exception:
                record["price"] = 0.0
            yield record


def _load_quotes(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        raise JudgeError(f"Quotes missing: {path}")
    return list(_iter_quotes(path))


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise JudgeError(f"Judge config missing: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - defensive
        raise JudgeError(f"Invalid judge config: {exc}") from exc


@dataclass
class JudgeWindow:
    name: str
    start_row: int
    count: int


def _parse_windows(cfg: dict) -> List[JudgeWindow]:
    windows_cfg = cfg.get("windows") or []
    windows: List[JudgeWindow] = []
    for idx, item in enumerate(windows_cfg, start=1):
        start_row = int(item.get("start_row") or 0)
        count = int(item.get("count") or 0)
        if start_row <= 0 or count <= 0:
            raise JudgeError(f"Invalid window #{idx}: start_row/count required")
        name = str(item.get("name") or f"window_{idx}")
        windows.append(JudgeWindow(name=name, start_row=start_row, count=count))
    if not windows:
        raise JudgeError("No judge windows configured")
    return windows


def _slice_window(quotes: List[Dict[str, object]], window: JudgeWindow) -> List[Dict[str, object]]:
    start_idx = max(0, window.start_row - 1)
    end_idx = start_idx + window.count
    return quotes[start_idx:end_idx]


def _kill_switch_paths() -> List[Path]:
    from tools.sim_autopilot import _kill_switch_enabled, _kill_switch_path

    cfg_path = ROOT / "config.yaml"
    cfg: dict = {}
    if cfg_path.exists():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            cfg = {}
    paths: List[Path] = [LOGS_DIR / "train_service" / "KILL_SWITCH"]
    if _kill_switch_enabled(cfg):
        paths.append(_kill_switch_path(cfg).expanduser().resolve())
    return paths


def _kill_switch_tripped() -> Tuple[bool, str]:
    for path in _kill_switch_paths():
        if path.exists():
            return True, str(path)
    return False, ""


def _risk_score(metrics: Dict[str, float | int]) -> float:
    from tools.sim_tournament import _score_run

    mapped = {
        "final_equity_usd": metrics.get("final_equity", 0.0),
        "max_drawdown_pct": metrics.get("max_drawdown", 0.0),
        "num_orders": metrics.get("turnover", 0),
        "num_risk_rejects": metrics.get("reject_count", 0),
        "num_postmortems": metrics.get("gate_triggers", 0),
    }
    return float(_score_run(mapped))


def _update_drawdown(equity: float, peak: float, max_dd: float) -> Tuple[float, float]:
    new_peak = max(peak, equity)
    drawdown = max_dd
    if new_peak > 0:
        drawdown = max(drawdown, (new_peak - equity) / new_peak)
    return new_peak, drawdown


def _simulate_baseline(quotes: List[Dict[str, object]], mode: str) -> Dict[str, float | int]:
    equity = INITIAL_CASH
    cash = INITIAL_CASH
    position = 0.0
    avg_cost = 0.0
    peak = INITIAL_CASH
    max_dd = 0.0
    turnover = 0
    for idx, row in enumerate(quotes):
        price = float(row.get("price") or 0.0)
        if idx == 0 and mode == "BuyHold" and price > 0:
            position = cash / price
            avg_cost = price
            cash = 0.0
            turnover += 1
        equity = cash + position * price
        peak, max_dd = _update_drawdown(equity, peak, max_dd)
    return {
        "final_equity": round(equity, 4),
        "max_drawdown": round(max_dd, 6),
        "turnover": turnover,
        "reject_count": 0,
        "gate_triggers": 0,
        "score": _risk_score(
            {
                "final_equity": equity,
                "max_drawdown": max_dd,
                "turnover": turnover,
                "reject_count": 0,
                "gate_triggers": 0,
            }
        ),
    }


def _simulate_policy(quotes: List[Dict[str, object]], policy: Dict[str, object], policy_version: str, logs_dir: Path) -> Dict[str, float | int]:
    from tools.sim_autopilot import run_step

    state: Dict[str, object] = {
        "cash_usd": INITIAL_CASH,
        "positions": {},
        "avg_cost": {},
        "risk_state": {
            "mode": "NORMAL",
            "equity": INITIAL_CASH,
            "start_equity": INITIAL_CASH,
            "peak_equity": INITIAL_CASH,
        },
    }
    peak = INITIAL_CASH
    max_dd = 0.0
    turnover = 0
    reject_count = 0
    gate_triggers = 0
    for row in quotes:
        tripped, reason = _kill_switch_tripped()
        if tripped:
            raise JudgeError(f"KILL_SWITCH present: {reason}")
        state, events = run_step(
            row,
            state,
            {
                "logs_dir": logs_dir,
                "verify_no_lookahead": True,
                "policy_version": policy_version,
                "risk_overrides": policy.get("risk_overrides", {}),
            },
        )
        risk_state = state.get("risk_state", {}) or {}
        equity = float(risk_state.get("equity", state.get("cash_usd", INITIAL_CASH)))
        peak, max_dd = _update_drawdown(equity, peak, max_dd)
        for event in events:
            if event.get("event_type") == "SIM_DECISION" and str(event.get("decision")).upper() != "ALLOW":
                reject_count += 1
            if event.get("event_type") == "SIM_INTENT":
                turnover += 1
        if risk_state.get("postmortem_triggered"):
            gate_triggers += 1
    return {
        "final_equity": round(float(state.get("risk_state", {}).get("equity", state.get("cash_usd", INITIAL_CASH))), 4),
        "max_drawdown": round(max_dd, 6),
        "turnover": turnover,
        "reject_count": reject_count,
        "gate_triggers": gate_triggers,
        "score": _risk_score(
            {
                "final_equity": float(state.get("risk_state", {}).get("equity", state.get("cash_usd", INITIAL_CASH))),
                "max_drawdown": max_dd,
                "turnover": turnover,
                "reject_count": reject_count,
                "gate_triggers": gate_triggers,
            }
        ),
    }


def _recommend(metrics: Dict[str, object], baseline_best: float) -> Tuple[str, str]:
    drawdown = float(metrics.get("max_drawdown") or 0.0)
    turnover = int(metrics.get("turnover") or 0)
    score = float(metrics.get("score") or 0.0)
    margin = score - baseline_best
    if drawdown > DRAWNDOWN_LIMIT:
        return "HOLD", f"max_drawdown={drawdown:.4f} exceeds {DRAWNDOWN_LIMIT}"
    if turnover > TURNOVER_LIMIT:
        return "HOLD", f"turnover={turnover} exceeds {TURNOVER_LIMIT}"
    if margin <= MARGIN_BUFFER:
        return "HOLD", f"score_margin={margin:.2f} below buffer {MARGIN_BUFFER:.2f}"
    return "PROMOTE", f"score_margin={margin:.2f}; drawdown={drawdown:.4f}; turnover={turnover}"


def _format_marker(tag: str, **fields: object) -> str:
    chunks = [tag] + [f"{k}={fields[k]}" for k in sorted(fields)]
    return "|".join(chunks)


def _latest_run_id(runs_root: Path) -> str:
    summaries = sorted(runs_root.glob("**/summary.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if summaries:
        return summaries[0].parent.name
    return "manual"


def _write_report(path: Path, payload: Dict[str, object]) -> None:
    lines = ["# PR11 Judge Report", "", f"Generated: {payload['generated_ts']}", ""]
    lines.append("## Summary")
    lines.append(f"- Status: {payload['status']}")
    lines.append(f"- Policy: {payload.get('policy_version', 'unknown')}")
    lines.append(f"- Baseline best score: {payload.get('baseline_best')}")
    lines.append(f"- Candidate score: {payload.get('policy_metrics', {}).get('score')}")
    lines.append(f"- Recommendation: {payload.get('recommendation', {}).get('status')}")
    lines.append(f"  - Reason: {payload.get('recommendation', {}).get('reason')}")
    lines.append("")
    lines.append("## Windows")
    for row in payload.get("windows", []):
        lines.append(f"### {row['name']}")
        lines.append(f"- Rows: start={row['start_row']} count={row['count']}")
        lines.append(f"- Policy final_equity={row['policy_metrics']['final_equity']} max_drawdown={row['policy_metrics']['max_drawdown']} score={row['policy_metrics']['score']:.4f}")
        lines.append(f"- DoNothing final_equity={row['baselines']['DoNothing']['final_equity']} score={row['baselines']['DoNothing']['score']:.4f}")
        lines.append(f"- BuyHold final_equity={row['baselines']['BuyHold']['final_equity']} score={row['baselines']['BuyHold']['score']:.4f}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate SIM progress against a fixed judge set")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--quotes", type=Path, default=DEFAULT_QUOTES)
    parser.add_argument("--reports-root", type=Path, default=REPORTS_ROOT)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    args = parser.parse_args(argv)

    status = "PASS"
    message = "ok"
    windows_payload: List[Dict[str, object]] = []
    summary_marker = ""
    baseline_marker = ""
    recommendation_marker = ""
    verify_flag = 1
    payload: Dict[str, object] | None = None

    print("JUDGE_START")
    try:
        quotes_path = args.quotes.expanduser().resolve()
        cfg_path = args.config.expanduser().resolve()
        quotes = _load_quotes(quotes_path)
        cfg = _load_yaml(cfg_path)
        windows = _parse_windows(cfg)
        policy_version, policy = get_policy()
        judge_id = uuid.uuid4().hex[:8]
        run_id = _latest_run_id(args.runs_root)
        logs_dir = LOGS_DIR / "train_service" / "judge_runs" / judge_id
        policy_scores: List[float] = []
        baseline_scores: Dict[str, List[float]] = {name: [] for name in BASELINES}
        for window in windows:
            subset = _slice_window(quotes, window)
            if not subset:
                raise JudgeError(f"Window {window.name} empty")
            policy_metrics = _simulate_policy(subset, policy, policy_version, logs_dir)
            baselines = {name: _simulate_baseline(subset, name) for name in BASELINES}
            windows_payload.append(
                {
                    "name": window.name,
                    "start_row": window.start_row,
                    "count": window.count,
                    "policy_metrics": policy_metrics,
                    "baselines": baselines,
                }
            )
            policy_scores.append(float(policy_metrics.get("score", 0.0)))
            for name in BASELINES:
                baseline_scores[name].append(float(baselines[name].get("score", 0.0)))
        avg_policy = sum(policy_scores) / len(policy_scores)
        avg_baselines = {name: (sum(scores) / len(scores) if scores else 0.0) for name, scores in baseline_scores.items()}
        baseline_best = max(avg_baselines.values()) if avg_baselines else 0.0
        summary_metrics = {
            "final_equity": round(sum(w["policy_metrics"]["final_equity"] for w in windows_payload) / len(windows_payload), 4),
            "max_drawdown": round(max(w["policy_metrics"]["max_drawdown"] for w in windows_payload), 6),
            "turnover": sum(int(w["policy_metrics"]["turnover"]) for w in windows_payload),
            "reject_count": sum(int(w["policy_metrics"]["reject_count"]) for w in windows_payload),
            "gate_triggers": sum(int(w["policy_metrics"]["gate_triggers"]) for w in windows_payload),
            "score": avg_policy,
        }
        rec_status, rec_reason = _recommend(summary_metrics, baseline_best)
        payload = {
            "generated_ts": _now().isoformat(),
            "status": status,
            "message": message,
            "policy_version": policy_version,
            "run_id": run_id,
            "judge_id": judge_id,
            "windows": windows_payload,
            "policy_metrics": summary_metrics,
            "baselines": avg_baselines,
            "baseline_best": baseline_best,
            "recommendation": {"status": rec_status, "reason": rec_reason},
            "verify_no_lookahead": verify_flag,
        }
    except JudgeError as exc:
        status = "DEGRADED"
        message = str(exc)
        payload = {
            "generated_ts": _now().isoformat(),
            "status": status,
            "message": message,
            "policy_version": "unknown",
            "run_id": "unknown",
            "judge_id": uuid.uuid4().hex[:8],
            "windows": [],
            "policy_metrics": {},
            "baselines": {},
            "baseline_best": 0.0,
            "recommendation": {"status": "HOLD", "reason": message},
            "verify_no_lookahead": verify_flag,
        }
    except Exception as exc:  # pragma: no cover - fail closed
        status = "FAIL"
        message = str(exc)
        payload = {
            "generated_ts": _now().isoformat(),
            "status": status,
            "message": message,
            "policy_version": "unknown",
            "run_id": "unknown",
            "judge_id": uuid.uuid4().hex[:8],
            "windows": windows_payload,
            "policy_metrics": {},
            "baselines": {},
            "baseline_best": 0.0,
            "recommendation": {"status": "HOLD", "reason": message},
            "verify_no_lookahead": verify_flag,
        }

    payload = payload or {}
    if payload:
        try:
            report_dir = args.reports_root / _now().strftime("%Y%m%d")
            report_path = report_dir / f"judge_{payload.get('judge_id', 'unknown')}.md"
            _write_report(report_path, payload)
        except Exception:
            pass
        try:
            _atomic_write_json(args.state_path, payload)
        except Exception:
            pass

    summary_marker = _format_marker(
        "JUDGE_SUMMARY",
        status=status,
        windows=len(payload.get("windows", [])) if isinstance(payload, dict) else 0,
        policy=payload.get("policy_version", "unknown") if isinstance(payload, dict) else "unknown",
        run_id=payload.get("run_id", "unknown") if isinstance(payload, dict) else "unknown",
        verify_no_lookahead=verify_flag,
        message=message.replace("|", " "),
    )
    baseline_marker = _format_marker(
        "BASELINE_COMPARISON",
        policy_score=round(float(payload.get("policy_metrics", {}).get("score", 0.0)), 4)
        if isinstance(payload, dict)
        else 0.0,
        best_baseline=round(float(payload.get("baseline_best", 0.0)), 4)
        if isinstance(payload, dict)
        else 0.0,
        do_nothing=round(float(payload.get("baselines", {}).get("DoNothing", 0.0)), 4)
        if isinstance(payload, dict)
        else 0.0,
        buy_hold=round(float(payload.get("baselines", {}).get("BuyHold", 0.0)), 4)
        if isinstance(payload, dict)
        else 0.0,
    )
    recommendation_marker = _format_marker(
        "PROMOTION_RECOMMENDATION",
        status=str(payload.get("recommendation", {}).get("status", "HOLD") if isinstance(payload, dict) else "HOLD"),
        reason=str(payload.get("recommendation", {}).get("reason", message) if isinstance(payload, dict) else message).replace("|", " "),
    )

    print(summary_marker)
    print(baseline_marker)
    print(recommendation_marker)
    print("JUDGE_END")
    print(summary_marker)
    print(baseline_marker)
    print(recommendation_marker)
    return 0 if status in {"PASS", "DEGRADED"} else 1


if __name__ == "__main__":
    raise SystemExit(run())
