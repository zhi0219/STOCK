from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
DEFAULT_RUNS_ROOT = LOGS_DIR / "train_runs"
PROGRESS_JUDGE_DIR = DEFAULT_RUNS_ROOT / "progress_judge"
LATEST_PATH = PROGRESS_JUDGE_DIR / "latest.json"
SUMMARY_TAG = "PROGRESS_JUDGE_SUMMARY"
KILL_SWITCH_ENV = "PR11_KILL_SWITCH"


def _summary_line(
    status: str,
    runs_root: Path,
    runs_scanned: int,
    issues: list[str],
    recommendation: str,
) -> str:
    reason = ";".join(issues) if issues else "ok"
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"recommendation={recommendation}",
            f"runs_root={runs_root}",
            f"runs_scanned={runs_scanned}",
            f"issues={len(issues)}",
            f"reason={reason}",
        ]
    )


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _validate_runs_root(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"runs_root not found: {resolved}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"runs_root is not a directory: {resolved}")
    return resolved


def _iter_runs(runs_root: Path) -> list[Path]:
    run_dirs: list[Path] = []
    for summary_json in runs_root.glob("**/summary.json"):
        run_dirs.append(summary_json.parent)
    if not run_dirs:
        for summary_md in runs_root.glob("**/summary.md"):
            run_dirs.append(summary_md.parent)
    return sorted(set(run_dirs))


def _scan_run(run_dir: Path, summary_path: Path) -> list[str]:
    issues: list[str] = []
    name_lower = run_dir.name.lower()
    if any(flag in name_lower for flag in ("live", "paper", "real")):
        issues.append(f"{run_dir.name}:suspicious_run_name")

    orders_files = list(run_dir.glob("orders*.jsonl"))
    sim_orders = [p for p in orders_files if "sim" in p.name.lower()]
    if orders_files and not sim_orders:
        issues.append(f"{run_dir.name}:orders_missing_sim_tag")
    elif not orders_files:
        issues.append(f"{run_dir.name}:orders_missing")

    for path in orders_files:
        lower = path.name.lower()
        if "live" in lower or "paper" in lower:
            issues.append(f"{run_dir.name}:disallowed_orders_file={path.name}")

    if not summary_path.exists():
        issues.append(f"{run_dir.name}:summary_missing")
    else:
        text = summary_path.read_text(encoding="utf-8", errors="replace")
        if "sim" not in text.lower():
            issues.append(f"{run_dir.name}:summary_not_marked_sim")
        if "stop reason" not in text.lower():
            issues.append(f"{run_dir.name}:summary_missing_stop_reason")

    equity_path = run_dir / "equity_curve.csv"
    if not equity_path.exists():
        issues.append(f"{run_dir.name}:equity_curve_missing")

    return issues


def _load_summary(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_equity_curve(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    return rows


def _volatility_proxy(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    returns = []
    for prev, cur in zip(values[:-1], values[1:]):
        if prev == 0:
            continue
        returns.append((cur - prev) / prev)
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns)


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _compute_judge_set_id(seed: int | None, run_ids: list[str]) -> str:
    seed_text = str(seed) if seed is not None else "none"
    digest = hashlib.sha256(f"{seed_text}|{'|'.join(run_ids)}".encode("utf-8")).hexdigest()
    return f"judge_{digest[:12]}"


def _recommendation_from_scores(mean_score: float | None) -> str:
    if mean_score is None:
        return "INSUFFICIENT_DATA"
    return "IMPROVING" if mean_score > 0 else "NOT_IMPROVING"


def _trend_direction(values: list[float]) -> str:
    if len(values) < 2:
        return "unknown"
    first = values[0]
    last = values[-1]
    if last > first + 1e-6:
        return "up"
    if last < first - 1e-6:
        return "down"
    return "flat"


def _build_run_record(run_dir: Path) -> tuple[dict[str, object], list[str]]:
    issues: list[str] = []
    summary_md = run_dir / "summary.md"
    summary_json = run_dir / "summary.json"
    equity_path = run_dir / "equity_curve.csv"
    summary_payload = _load_summary(summary_json) or {}

    if not summary_payload:
        issues.append(f"{run_dir.name}:summary_json_missing_or_invalid")

    equity_rows = _load_equity_curve(equity_path)
    equity_values = [float(row.get("equity_usd", 0.0)) for row in equity_rows if row.get("equity_usd")]
    volatility = _volatility_proxy(equity_values)

    turnover = _coerce_float(summary_payload.get("turnover"))
    rejects_count = _coerce_float(summary_payload.get("rejects_count"))
    reject_rate = None
    if turnover is not None and rejects_count is not None:
        reject_rate = rejects_count / max(1.0, turnover)

    record = {
        "run_id": summary_payload.get("run_id") or run_dir.name,
        "run_dir": str(run_dir),
        "summary_path": str(summary_md) if summary_md.exists() else "",
        "summary_json_path": str(summary_json) if summary_json.exists() else "",
        "equity_path": str(equity_path) if equity_path.exists() else "",
        "policy_version": summary_payload.get("policy_version") or "unknown",
        "timestamps": summary_payload.get("timestamps") if isinstance(summary_payload.get("timestamps"), dict) else {},
        "net_change": _coerce_float(summary_payload.get("net_change")),
        "max_drawdown": _coerce_float(summary_payload.get("max_drawdown")),
        "turnover": turnover,
        "rejects_count": rejects_count,
        "reject_rate": reject_rate,
        "volatility_proxy": volatility,
    }
    return record, issues


def _write_run_judge(run_dir: Path, payload: dict[str, object]) -> None:
    judge_path = run_dir / "judge.json"
    _atomic_write(judge_path, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge SIM-only training progress runs")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--max-runs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)

    issues: list[str] = []
    recommendation = "INSUFFICIENT_DATA"
    runs_scanned = 0
    run_records: list[dict[str, object]] = []
    used_records: list[dict[str, object]] = []
    start_ts = None
    end_ts = None
    try:
        if os.environ.get(KILL_SWITCH_ENV):
            issues.append("kill_switch_engaged")
            raise RuntimeError("Kill switch active")

        runs_root = _validate_runs_root(args.runs_root)
        run_dirs = _iter_runs(runs_root)
        runs_scanned = len(run_dirs)
        if not run_dirs:
            issues.append("no_runs_found")
            raise RuntimeError("No runs available for judging")

        sorted_runs = sorted(
            run_dirs,
            key=lambda p: (p / "summary.json").stat().st_mtime if (p / "summary.json").exists() else p.stat().st_mtime,
            reverse=True,
        )

        for run_dir in sorted_runs[: max(args.max_runs, 1)]:
            summary_md = run_dir / "summary.md"
            issues.extend(_scan_run(run_dir, summary_md))
            record, record_issues = _build_run_record(run_dir)
            issues.extend(record_issues)
            run_records.append(record)
            used_records.append(record)

            if record.get("timestamps"):
                timestamps = record.get("timestamps") or {}
                start_val = timestamps.get("start")
                end_val = timestamps.get("end")
                if start_val and (start_ts is None or str(start_val) < str(start_ts)):
                    start_ts = start_val
                if end_val and (end_ts is None or str(end_val) > str(end_ts)):
                    end_ts = end_val

            _write_run_judge(
                run_dir,
                {
                    "schema_version": "1.0",
                    "run_id": record.get("run_id"),
                    "policy_version": record.get("policy_version"),
                    "net_change": record.get("net_change"),
                    "max_drawdown": record.get("max_drawdown"),
                    "turnover": record.get("turnover"),
                    "rejects_count": record.get("rejects_count"),
                    "reject_rate": record.get("reject_rate"),
                    "volatility_proxy": record.get("volatility_proxy"),
                    "summary_path": record.get("summary_path"),
                    "summary_json_path": record.get("summary_json_path"),
                    "equity_path": record.get("equity_path"),
                    "generated_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
    except Exception as exc:  # pragma: no cover - defensive fail closed
        if "kill_switch_engaged" not in issues:
            issues.append(str(exc))

    numeric_scores = [rec.get("net_change") for rec in used_records if isinstance(rec.get("net_change"), (int, float))]
    mean_score = statistics.mean(numeric_scores) if numeric_scores else None
    recommendation = _recommendation_from_scores(mean_score)

    max_drawdowns = [rec.get("max_drawdown") for rec in used_records if isinstance(rec.get("max_drawdown"), (int, float))]
    turnovers = [rec.get("turnover") for rec in used_records if isinstance(rec.get("turnover"), (int, float))]
    reject_rates = [rec.get("reject_rate") for rec in used_records if isinstance(rec.get("reject_rate"), (int, float))]
    vols = [rec.get("volatility_proxy") for rec in used_records if isinstance(rec.get("volatility_proxy"), (int, float))]

    drivers: list[str] = []
    if mean_score is not None and mean_score > 0:
        drivers.append("Average net change positive")
    if max_drawdowns and max(max_drawdowns) <= 5:
        drivers.append("Max drawdown stayed within 5%")
    if turnovers and statistics.mean(turnovers) <= 5:
        drivers.append("Turnover stayed low")
    if reject_rates and statistics.mean(reject_rates) <= 0.2:
        drivers.append("Reject rate stayed under 20%")
    drivers = drivers[:3]

    not_improving: list[str] = []
    suggestions: list[str] = []
    if recommendation == "NOT_IMPROVING":
        not_improving.append("Average net change is non-positive vs baseline.")
        suggestions.append("Review recent SIM runs for drawdown or turnover regressions.")
    if recommendation == "INSUFFICIENT_DATA":
        not_improving.append("Missing or incomplete run metrics for judging.")
        suggestions.append("Run additional SIM sessions with summaries and equity curves.")

    policy_versions = {rec.get("policy_version") for rec in used_records if rec.get("policy_version")}
    policy_version = policy_versions.pop() if len(policy_versions) == 1 else "mixed"

    judge_set_id = _compute_judge_set_id(args.seed, [str(rec.get("run_id")) for rec in used_records])
    recent_scores = numeric_scores[:5]
    trend_values = list(reversed(recent_scores))
    latest_payload = {
        "schema_version": "1.0",
        "judge_set_id": judge_set_id,
        "generated_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs_root": str(args.runs_root),
        "policy_version": policy_version,
        "time_window": {"start": start_ts, "end": end_ts},
        "baseline": {"do_nothing": 0.0, "buy_hold": None, "buy_hold_available": False},
        "scores": {"vs_do_nothing": mean_score, "vs_buy_hold": None},
        "risk_metrics": {
            "max_drawdown": max(max_drawdowns) if max_drawdowns else None,
            "turnover": statistics.mean(turnovers) if turnovers else None,
            "reject_rate": statistics.mean(reject_rates) if reject_rates else None,
            "volatility_proxy": statistics.mean(vols) if vols else None,
        },
        "recommendation": recommendation,
        "recommendation_reasons": drivers if drivers else ["No positive drivers detected."],
        "drivers": drivers,
        "not_improving_reasons": not_improving,
        "suggested_next_actions": suggestions,
        "trend": {
            "window": len(trend_values),
            "direction": _trend_direction(trend_values),
            "values": trend_values,
        },
        "evidence": {
            "run_ids": [rec.get("run_id") for rec in used_records],
            "summaries": [
                {
                    "run_id": rec.get("run_id"),
                    "summary_path": rec.get("summary_path"),
                    "summary_json_path": rec.get("summary_json_path"),
                }
                for rec in used_records
            ],
        },
        "issues": issues,
    }
    _atomic_write(LATEST_PATH, latest_payload)

    status = "PASS" if not issues else "FAIL"
    summary = _summary_line(status, args.runs_root, runs_scanned, issues, recommendation)

    print("PROGRESS_JUDGE_START")
    print(summary)
    if issues:
        print("DETAILS:")
        for issue in issues:
            print(f"- {issue}")
    else:
        print("All inspected runs are SIM-only and well-formed.")
    print(summary)
    print("PROGRESS_JUDGE_END")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
