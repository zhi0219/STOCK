from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Sequence, Tuple
from zipfile import ZIP_DEFLATED, ZipFile

import yaml

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.policy_registry import get_policy
from tools.policy_registry import promote_policy as _promote_policy
from tools.sim_autopilot import _kill_switch_enabled, _kill_switch_path, run_step
from tools.sim_tournament import (
    RUNS_DIR as TOURNAMENT_RUNS,
    _load_quotes,
    _render_report,
    _run_single,
    _score_run,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "Data" / "quotes.csv"
RUNS_ROOT = ROOT / "Logs" / "train_runs"
STATE_PATH = ROOT / "Logs" / "train_daemon_state.json"
EVENTS_PATH = ROOT / "Logs" / "events_train.jsonl"
ARCHIVES_ROOT = ROOT / "Archives"
EVIDENCE_CORE = [
    ROOT / "evidence_packs",
    ROOT / "qa_packets",
    ROOT / "qa_answers",
    ROOT / "Logs",
    ROOT / "Reports",
]


@dataclass
class RetentionResult:
    deleted_paths: List[Path]
    freed_mb: float
    kept: int
    total_mb: float


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iter_rows(quotes: List[Dict[str, object]]) -> Iterable[Dict[str, object]]:
    for row in quotes:
        yield row


def _calc_drawdown(equities: List[float]) -> float:
    peak = equities[0] if equities else 0.0
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        if peak <= 0:
            continue
        max_dd = max(max_dd, (peak - eq) / peak)
    return max_dd


def _validate_runs_root(runs_root: Path) -> Path:
    allowed_root = RUNS_ROOT.resolve()
    candidate = runs_root.expanduser().resolve()
    try:
        candidate.relative_to(allowed_root)
    except ValueError:
        if candidate != allowed_root:
            raise ValueError(
                f"runs_root must be within {allowed_root}; got {candidate}"
            )
    if not candidate.is_dir():
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _list_run_dirs(runs_root: Path) -> List[Path]:
    run_dirs: List[Path] = []
    for day_dir in runs_root.iterdir():
        if not day_dir.is_dir():
            continue
        for run_dir in day_dir.iterdir():
            if run_dir.is_dir():
                run_dirs.append(run_dir)
    return run_dirs


def _write_equity_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames = [
        "ts_utc",
        "equity_usd",
        "cash_usd",
        "drawdown_pct",
        "step",
        "policy_version",
        "mode",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def _log_size_mb(root: Path) -> float:
    total = 0
    if not root.exists():
        return 0.0
    for item in root.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total / (1024 * 1024)


def _format_retention_summary(result: RetentionResult) -> str:
    return (
        f"deleted={len(result.deleted_paths)} | "
        f"freed_mb={result.freed_mb:.2f} | "
        f"kept={result.kept} | total_mb={result.total_mb:.2f}"
    )


def _retention_sweep(
    runs_root: Path,
    retain_days: int,
    retain_latest_n: int,
    max_total_train_runs_mb: int,
    dry_run: bool = False,
) -> RetentionResult:
    allowed_root = RUNS_ROOT.resolve()
    runs_root = _validate_runs_root(runs_root)
    if not runs_root.is_dir():
        return RetentionResult([], 0.0, 0, 0.0)

    run_dirs = _list_run_dirs(runs_root)
    now = _now()
    cutoff = now - timedelta(days=max(retain_days, 0))
    entries: List[Tuple[Path, datetime, float]] = []
    for run_dir in run_dirs:
        stat = run_dir.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        size_mb = _log_size_mb(run_dir)
        entries.append((run_dir, mtime, size_mb))

    entries.sort(key=lambda item: item[1])
    deleted: List[Path] = []
    freed_mb = 0.0

    def _should_stop(active: Sequence[Tuple[Path, datetime, float]]) -> bool:
        if not active:
            return True
        oldest_mtime = active[0][1]
        active_total = sum(item[2] for item in active)
        if len(active) > retain_latest_n:
            return False
        if oldest_mtime < cutoff:
            return False
        if active_total > max_total_train_runs_mb:
            return False
        return True

    active_entries = list(entries)
    print("RETENTION_START")
    while active_entries and not _should_stop(active_entries):
        victim, _, victim_size = active_entries.pop(0)
        victim_resolved = victim.resolve()
        try:
            victim_resolved.relative_to(allowed_root)
        except ValueError:
            raise ValueError(f"Unsafe deletion target detected: {victim_resolved}")

        print(f"RETENTION_DELETE|path={victim_resolved}|mb={victim_size:.2f}")
        deleted.append(victim_resolved)
        freed_mb += victim_size
        if not dry_run:
            shutil.rmtree(victim_resolved)
    remaining_total = sum(item[2] for item in active_entries)
    result = RetentionResult(
        deleted_paths=deleted,
        freed_mb=freed_mb,
        kept=len(active_entries),
        total_mb=remaining_total,
    )
    print(
        "RETENTION_SUMMARY|"
        f"deleted={len(deleted)}|freed_mb={freed_mb:.2f}|"
        f"kept={len(active_entries)}|total_mb={remaining_total:.2f}"
    )
    return result


def _summary_md(
    run_id: str,
    policy_version: str,
    equity_rows: List[Dict[str, object]],
    trade_count: int,
    rejects: Counter,
    stop_reason: str,
    outputs: Dict[str, Path],
) -> str:
    equities = [float(row.get("equity_usd", 0.0)) for row in equity_rows]
    net_change = (equities[-1] - equities[0]) if len(equities) >= 2 else 0.0
    max_dd = _calc_drawdown(equities) * 100 if equities else 0.0
    best = max(equity_rows, key=lambda r: r.get("equity_usd", 0.0), default=None)
    worst = min(equity_rows, key=lambda r: r.get("equity_usd", 0.0), default=None)
    lines = [
        "# Train Daemon Summary",
        "",
        f"Run: {run_id}",
        f"Policy: {policy_version}",
        f"Stop reason: {stop_reason}",
        f"Net value change: {net_change:+.2f} USD",
        f"Max drawdown: {max_dd:.2f}%",
        f"Trades executed: {trade_count}",
        "",
        "## Rejection reasons (top 5)",
    ]
    for reason, count in rejects.most_common(5):
        lines.append(f"- {reason}: {count}")
    if not rejects:
        lines.append("- None recorded")

    lines.append("")
    lines.append("## Best/Worst equity points")
    if best:
        lines.append(
            f"- Best: step {best.get('step')} at {best.get('ts_utc')} (equity_curve.csv)"
        )
    if worst:
        lines.append(
            f"- Worst: step {worst.get('step')} at {worst.get('ts_utc')} (equity_curve.csv)"
        )
    if not best and not worst:
        lines.append("- No equity points recorded")

    lines.append("")
    lines.append("## Outputs")
    for label, path in outputs.items():
        lines.append(f"- {label}: {path}")
    lines.append("")
    lines.append("All runs are SIM-only; no live trading endpoints are touched.")
    return "\n".join(lines)


def _append_retention_to_summary(path: Path, retention_result: RetentionResult) -> None:
    lines = ["", "## Retention", f"- {_format_retention_summary(retention_result)}"]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_config() -> dict:
    cfg_path = ROOT / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _git_rev() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        return None
    return None


def _env_fingerprint(policy_version: str) -> Dict[str, object]:
    return {
        "sys_executable": sys.executable,
        "repo_root": str(ROOT),
        "policy_version": policy_version,
        "git_commit": _git_rev(),
    }


def _write_event(event_type: str, message: str, severity: str = "INFO", **extra: object) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "schema_version": 1,
        "ts_utc": _now().isoformat(),
        "event_type": event_type,
        "symbol": "-",
        "severity": severity,
        "message": message,
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def _write_state(payload: Dict[str, object]) -> None:
    _atomic_write_json(STATE_PATH, payload)


def _quotes_health(quotes: List[Dict[str, object]]) -> Tuple[bool, str, Dict[str, object]]:
    if not quotes:
        return False, "missing", {"count": 0}
    prices = [float(row.get("price") or 0.0) for row in quotes]
    if len(set(prices)) <= 1:
        return False, "flat", {"count": len(prices)}
    timestamps: List[datetime] = []
    for row in quotes:
        raw = row.get("ts_utc") or row.get("ts")
        try:
            ts = datetime.fromisoformat(str(raw))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            timestamps.append(ts)
        except Exception:
            continue
    if timestamps:
        newest = max(timestamps)
        if (_now() - newest) > timedelta(days=365):
            return False, "stale", {"newest": newest.isoformat()}
    return True, "ok", {"count": len(prices), "newest_ts": timestamps[-1].isoformat() if timestamps else None}


def _tournament_report(
    quotes: List[Dict[str, object]],
    policy_version: str,
    max_steps: int,
) -> Tuple[List[Dict[str, object]], Path]:
    windows = []
    timestamps: List[datetime] = []
    for row in quotes:
        raw = row.get("ts_utc") or row.get("ts")
        try:
            ts = datetime.fromisoformat(str(raw))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            timestamps.append(ts)
        except Exception:
            continue
    if timestamps:
        windows.append((min(timestamps), max(timestamps)))
    else:
        now = _now()
        windows.append((now - timedelta(days=1), now))

    runs: List[Dict[str, object]] = []
    for window in windows:
        run_id, metrics = _run_single(
            quotes,
            window,
            "baseline",
            max_steps,
            policy_version,
            {},
        )
        metrics["run_id"] = run_id
        metrics["window_start"] = window[0].isoformat()
        metrics["window_end"] = window[1].isoformat()
        metrics["score"] = _score_run(metrics)
        runs.append(metrics)

    summary = {
        "generated_at": _now().isoformat(),
        "runs": runs,
        "policy_version": policy_version,
    }
    TOURNAMENT_RUNS.mkdir(parents=True, exist_ok=True)
    report_path = TOURNAMENT_RUNS / f"tournament_summary_{policy_version}_train.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md = TOURNAMENT_RUNS / f"tournament_report_{policy_version}_train.md"
    _render_report(runs, report_md)
    return runs, report_md


def _generate_guard_proposal(run: Dict[str, object], policy_version: str) -> Dict[str, object]:
    proposal = {
        "event_type": "GUARD_PROPOSAL",
        "proposal": {
            "max_drawdown": max(0.005, float(run.get("max_drawdown_pct", 0.0)) * 0.9),
            "max_orders_per_minute": max(1, int(run.get("num_orders", 0)) // 10 + 1),
        },
        "trigger_sample": run.get("run_id"),
        "metrics": {
            "drawdown": float(run.get("max_drawdown_pct", 0.0)),
            "rejects": int(run.get("num_risk_rejects", 0)),
        },
        "policy_version": policy_version,
    }
    _write_event(
        "GUARD_PROPOSAL",
        "Guard proposal generated",
        source_run=run.get("run_id"),
        proposal=proposal.get("proposal"),
        seed=run.get("seed"),
    )
    return proposal


def _maybe_generate_candidate(events_path: Path | None = None) -> Tuple[str | None, Path | None]:
    candidate_script = ROOT / "tools" / "policy_candidate.py"
    cmd = [sys.executable, str(candidate_script)]
    if events_path:
        cmd.extend(["--events", str(events_path)])
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        _write_event(
            "POLICY_CANDIDATE_CREATED",
            "Failed to generate candidate",
            severity="WARN",
            stderr=result.stderr,
        )
        return None, None
    marker = None
    for line in result.stdout.splitlines():
        if line.strip().startswith("Generated"):
            marker = line.strip().split()[1]
    events = EVENTS_PATH if events_path is None else events_path
    _write_event(
        "POLICY_CANDIDATE_CREATED",
        "Policy candidate generated",
        candidate_version=marker,
        events_path=str(events),
    )
    return marker, events


def _promotion_decision(candidate_version: str | None, auto_promote: bool, evidence_path: Path | None, gate_summary: Dict[str, object]) -> None:
    decision = "not_applicable"
    if candidate_version:
        decision = "not_recommended"
        if auto_promote and gate_summary.get("pass", False):
            _promote_policy(candidate_version, evidence=str(evidence_path) if evidence_path else "")
            decision = "promoted"
            _write_event(
                "POLICY_PROMOTED",
                "Policy promoted via auto gate",
                candidate_version=candidate_version,
                evidence=str(evidence_path) if evidence_path else None,
            )
    _write_event(
        "PROMOTION_DECISION",
        "Promotion gate evaluated",
        candidate_version=candidate_version,
        decision=decision,
        gate_metrics=gate_summary,
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SIM-only training daemon", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Quotes CSV for simulation")
    parser.add_argument("--runs-root", default=str(RUNS_ROOT), help="Root directory for run outputs")
    parser.add_argument("--max-runtime-seconds", type=int, default=3600, dest="max_runtime_seconds")
    parser.add_argument("--max-steps", type=int, default=5000, dest="max_steps")
    parser.add_argument("--max-trades", type=int, default=500, dest="max_trades")
    parser.add_argument("--max-log-mb", type=float, default=128.0, dest="max_log_mb")
    parser.add_argument("--retain-days", type=int, default=7, dest="retain_days")
    parser.add_argument("--retain-latest-n", type=int, default=50, dest="retain_latest_n")
    parser.add_argument(
        "--max-total-train-runs-mb",
        type=int,
        default=5000,
        dest="max_total_train_runs_mb",
    )
    parser.add_argument(
        "--retention-dry-run",
        action="store_true",
        dest="retention_dry_run",
        help="Only print the deletion plan without executing",
    )
    parser.add_argument(
        "--archive-evidence-days",
        type=int,
        default=0,
        dest="archive_evidence_days",
        help="Archive Evidence Core older than N days (0 disables)",
    )
    parser.add_argument(
        "--archive-delete-source",
        action="store_true",
        dest="archive_delete_source",
        help="Delete Evidence Core files after archiving (requires --i-know-what-im-doing)",
    )
    parser.add_argument(
        "--i-know-what-im-doing",
        action="store_true",
        dest="archive_force",
        help="Explicit confirmation needed for destructive archive deletes",
    )
    parser.add_argument("--policy-version", dest="policy_version", help="Policy version override")
    parser.add_argument("--momentum-threshold", type=float, default=0.5, dest="momentum_threshold")
    parser.add_argument("--nightly", action="store_true", help="Preset for overnight runs (8h budget)")
    parser.add_argument("--seed", type=int, default=None, help="Seed for deterministic sampling")
    parser.add_argument("--max-iterations-per-day", type=int, default=1, dest="max_iterations_per_day")
    parser.add_argument("--max-events-per-hour", type=int, default=200, dest="max_events_per_hour")
    parser.add_argument("--max-disk-mb", type=float, default=10_000.0, dest="max_disk_mb")
    parser.add_argument("--max-runtime-per-day", type=int, default=8 * 3600, dest="max_runtime_per_day")
    parser.add_argument("--auto-promote", action="store_true", help="Allow auto promotion if gates pass")
    parser.add_argument("--backoff-seconds", type=int, default=2, dest="backoff_seconds")
    return parser.parse_args(argv)


def _apply_nightly_defaults(args: argparse.Namespace) -> None:
    if args.nightly:
        args.max_runtime_seconds = max(args.max_runtime_seconds, 8 * 60 * 60)
        args.max_steps = max(args.max_steps, 50_000)
        args.max_trades = max(args.max_trades, 10_000)
        args.max_log_mb = max(args.max_log_mb, 512.0)


def _archive_evidence_core(
    archive_days: int, delete_source: bool, force_delete: bool
) -> Tuple[Path | None, List[Path]]:
    if archive_days <= 0:
        return None, []
    cutoff = _now() - timedelta(days=archive_days)
    candidates: List[Path] = []
    for root in EVIDENCE_CORE:
        if not root.exists():
            continue
        if root.name == "Logs":
            for candidate in root.glob("events*.jsonl"):
                if candidate.is_file() and datetime.fromtimestamp(
                    candidate.stat().st_mtime, tz=timezone.utc
                ) < cutoff:
                    candidates.append(candidate)
            continue
        if root.is_dir():
            for candidate in root.rglob("*"):
                if candidate.is_file() and datetime.fromtimestamp(
                    candidate.stat().st_mtime, tz=timezone.utc
                ) < cutoff:
                    candidates.append(candidate)

    if not candidates:
        return None, []

    archive_root = ARCHIVES_ROOT.expanduser().resolve()
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_name = f"{_now().strftime('%Y%m%d')}_evidence.zip"
    archive_path = archive_root / archive_name
    suffix = 1
    while archive_path.exists():
        archive_path = archive_root / f"{_now().strftime('%Y%m%d')}_evidence_{suffix}.zip"
        suffix += 1

    archived_rel: List[str] = []
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as zf:
        for path in candidates:
            rel = path.resolve().relative_to(ROOT)
            zf.write(path, rel)
            archived_rel.append(str(rel))

    index_payload = {
        "created": _now().isoformat(),
        "cutoff_days": archive_days,
        "files": archived_rel,
        "delete_source": bool(delete_source),
    }
    (archive_root / "index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    deleted: List[Path] = []
    if delete_source:
        if not force_delete:
            raise ValueError("--archive-delete-source requires --i-know-what-im-doing")
        for path in candidates:
            rel = path.resolve().relative_to(ROOT)
            if rel.parts and rel.parts[0] not in {p.name for p in EVIDENCE_CORE}:
                raise ValueError(f"Unsafe archive deletion target: {path}")
            path.unlink()
            deleted.append(path)

    return archive_path, deleted


def _budget_tracker(max_events_per_hour: int) -> Dict[str, object]:
    return {"events": deque(), "max": max_events_per_hour}


def _record_event_budget(tracker: Dict[str, object]) -> bool:
    max_events = int(tracker.get("max", 0))
    if max_events <= 0:
        return True
    history: Deque[datetime] = tracker.get("events")  # type: ignore[assignment]
    now = _now()
    cutoff = now - timedelta(hours=1)
    while history and history[0] < cutoff:
        history.popleft()
    if len(history) >= max_events:
        return False
    history.append(now)
    return True


def _budget_ok(args: argparse.Namespace, start_ts: datetime, events_tracker: Dict[str, object]) -> Tuple[bool, str]:
    if not _record_event_budget(events_tracker):
        return False, "max_events_per_hour"
    if args.max_runtime_per_day and (_now() - start_ts).total_seconds() > args.max_runtime_per_day:
        return False, "max_runtime_per_day"
    if _log_size_mb(ROOT / "Logs") > float(args.max_disk_mb):
        return False, "max_disk_mb"
    return True, "ok"


def _run_simulation(
    quotes: List[Dict[str, object]],
    policy_version: str,
    policy_cfg: Dict[str, object],
    args: argparse.Namespace,
    run_dir: Path,
    kill_cfg: Dict[str, object],
) -> Tuple[str, Dict[str, object], List[Dict[str, object]], Counter, int]:
    sim_state: Dict[str, object] = {
        "cash_usd": 10_000.0,
        "risk_state": {
            "mode": "NORMAL",
            "equity": 10_000.0,
            "start_equity": 10_000.0,
            "peak_equity": 10_000.0,
        },
    }

    equity_rows: List[Dict[str, object]] = []
    rejects: Counter = Counter()
    trade_count = 0
    first_ts: str | None = None
    last_ts: str | None = None
    stop_reason = "budget_exhausted"

    step_limit = int(args.max_steps)
    trade_limit = int(args.max_trades)
    max_runtime = float(args.max_runtime_seconds)
    log_limit = float(args.max_log_mb)

    start_monotonic = time.monotonic()
    for step_no, row in enumerate(_iter_rows(quotes), start=1):
        now = _now()
        elapsed = time.monotonic() - start_monotonic
        if elapsed >= max_runtime:
            stop_reason = "max_runtime_seconds"
            break
        if step_no > step_limit:
            stop_reason = "max_steps"
            break
        if trade_count >= trade_limit:
            stop_reason = "max_trades"
            break
        if _kill_switch_enabled(kill_cfg) and _kill_switch_path(kill_cfg).expanduser().resolve().exists():
            stop_reason = "kill_switch"
            break
        if _log_size_mb(run_dir) > log_limit:
            stop_reason = "max_log_mb"
            break

        sim_state, emitted = run_step(
            row,
            sim_state,
            {
                "logs_dir": run_dir,
                "momentum_threshold_pct": args.momentum_threshold,
                "verify_no_lookahead": True,
                "policy_version": policy_version,
                "risk_overrides": policy_cfg.get("risk_overrides", {}),
            },
        )

        decision_events = [e for e in emitted if e.get("decision")]
        for event in decision_events:
            decision = str(event.get("decision"))
            reason = str(event.get("reason") or "")
            if decision == "ALLOW":
                trade_count += 1
            elif reason:
                rejects[reason] += 1

        risk_state = sim_state.get("risk_state", {}) or {}
        equity = float(risk_state.get("equity", sim_state.get("cash_usd", 0.0)))
        cash = float(sim_state.get("cash_usd", 0.0))
        peak = float(risk_state.get("peak_equity", equity)) or equity
        drawdown_pct = ((peak - equity) / peak * 100.0) if peak else 0.0
        ts_raw = row.get("ts_utc") or row.get("ts")
        ts = str(ts_raw) if ts_raw else now.isoformat()
        if first_ts is None:
            first_ts = ts
        last_ts = ts
        equity_rows.append(
            {
                "ts_utc": ts,
                "equity_usd": round(equity, 2),
                "cash_usd": round(cash, 2),
                "drawdown_pct": round(drawdown_pct, 4),
                "step": step_no,
                "policy_version": policy_version,
                "mode": risk_state.get("mode", "UNKNOWN"),
            }
        )

    if stop_reason == "budget_exhausted":
        stop_reason = "input_exhausted"

    meta = {
        "first_row_ts": first_ts,
        "last_row_ts": last_ts,
        "stop_reason": stop_reason,
        "steps_completed": len(equity_rows),
        "trades": trade_count,
    }
    return stop_reason, meta, equity_rows, rejects, trade_count


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    _apply_nightly_defaults(args)

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    input_path = input_path.expanduser().resolve()
    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}")
        return 1

    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = ROOT / runs_root
    try:
        runs_root = _validate_runs_root(runs_root)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    archive_path, archived_deleted = _archive_evidence_core(
        archive_days=int(args.archive_evidence_days),
        delete_source=bool(args.archive_delete_source),
        force_delete=bool(args.archive_force),
    )
    if archive_path:
        print(f"EVIDENCE_ARCHIVE={archive_path}")
    if archived_deleted:
        print(f"EVIDENCE_ARCHIVE_DELETIONS={len(archived_deleted)}")

    _ = _retention_sweep(
        runs_root,
        retain_days=int(args.retain_days),
        retain_latest_n=int(args.retain_latest_n),
        max_total_train_runs_mb=int(args.max_total_train_runs_mb),
        dry_run=bool(args.retention_dry_run),
    )

    policy_version, policy_cfg = get_policy(args.policy_version)
    kill_cfg = _load_config()
    quotes = _load_quotes(input_path)
    healthy, reason, metrics = _quotes_health(quotes)
    degraded_flags: List[str] = []

    if not healthy:
        degraded_flags.append("DATA_UNHEALTHY")
        _write_event("SIM_DATA_UNHEALTHY", f"Quotes unhealthy: {reason}", severity="ERROR", metrics=metrics)
        _write_state(
            {
                "run_id": None,
                "stage": "data_unhealthy",
                "last_success_ts": None,
                "last_report_path": None,
                "policy_version": policy_version,
                "degraded_flags": degraded_flags,
                "stop_reason": reason,
            }
        )
        time.sleep(max(1, int(args.backoff_seconds)))
        return 1

    seed = args.seed if args.seed is not None else random.randint(1, 1_000_000)
    random.seed(seed)
    start_ts = _now()
    run_id = f"train_{start_ts.strftime('%Y%m%d_%H%M%S')}_{seed}".replace(":", "")
    run_dir = runs_root / start_ts.strftime("%Y%m%d") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = _env_fingerprint(policy_version)
    _write_event("TRAIN_TICK", "train_daemon iteration start", policy_version=policy_version, fingerprint=fingerprint, run_id=run_id)

    budget_tracker = _budget_tracker(int(args.max_events_per_hour))
    ok, stop = _budget_ok(args, start_ts, budget_tracker)
    if not ok:
        _write_event("TRAIN_BUDGET_EXHAUSTED", "Budget exhausted", stop_reason=stop)
        return 1

    stop_reason, meta, equity_rows, rejects, trade_count = _run_simulation(
        quotes, policy_version, policy_cfg, args, run_dir, kill_cfg
    )
    if stop_reason == "kill_switch":
        _write_event(
            "TRAIN_STOPPED_KILL_SWITCH",
            "Kill switch engaged; stopping immediately",
            severity="ERROR",
            kill_switch_path=str(_kill_switch_path(kill_cfg).expanduser().resolve()),
        )

    outputs = {
        "equity_curve.csv": run_dir / "equity_curve.csv",
        "orders_sim.jsonl": run_dir / "orders_sim.jsonl",
        "summary.md": run_dir / "summary.md",
    }
    _write_equity_csv(outputs["equity_curve.csv"], equity_rows)

    run_meta = {
        "run_id": run_id,
        "seed": seed,
        "input": str(input_path),
        "policy_version": policy_version,
        "start_ts": start_ts.isoformat(),
        "end_ts": _now().isoformat(),
        "params": {
            "max_runtime_seconds": float(args.max_runtime_seconds),
            "max_steps": int(args.max_steps),
            "max_trades": int(args.max_trades),
            "max_log_mb": float(args.max_log_mb),
            "momentum_threshold": args.momentum_threshold,
            "verify_no_lookahead": True,
        },
        **meta,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_body = _summary_md(
        run_id=run_id,
        policy_version=policy_version,
        equity_rows=equity_rows,
        trade_count=trade_count,
        rejects=rejects,
        stop_reason=stop_reason,
        outputs={k: v.name for k, v in outputs.items()},
    )
    outputs["summary.md"].write_text(summary_body, encoding="utf-8")

    runs, report_md = _tournament_report(quotes, policy_version, max_steps=min(200, args.max_steps))
    worst_run = min(runs, key=lambda r: r.get("score", 0)) if runs else {}
    _write_event(
        "TOURNAMENT_DONE",
        "Tournament batch complete",
        report_path=str(report_md),
        metrics={"best_score": max((r.get("score", 0) for r in runs), default=0), "worst_score": worst_run.get("score")},
    )

    proposal = _generate_guard_proposal(worst_run, policy_version) if worst_run else None
    candidate_version, candidate_events_path = _maybe_generate_candidate(
        TOURNAMENT_RUNS / worst_run.get("run_id", "") / "events.jsonl" if worst_run else None
    )

    gate_summary = {
        "max_drawdown": worst_run.get("max_drawdown_pct"),
        "turnover": worst_run.get("num_orders"),
        "pass": bool(worst_run and worst_run.get("max_drawdown_pct", 0) < 5.0),
    }
    _promotion_decision(candidate_version, bool(args.auto_promote), report_md, gate_summary)

    end_retention = _retention_sweep(
        runs_root,
        retain_days=int(args.retain_days),
        retain_latest_n=int(args.retain_latest_n),
        max_total_train_runs_mb=int(args.max_total_train_runs_mb),
        dry_run=bool(args.retention_dry_run),
    )
    _append_retention_to_summary(outputs["summary.md"], end_retention)

    _write_state(
        {
            "run_id": run_id,
            "stage": "complete",
            "last_success_ts": _now().isoformat(),
            "last_report_path": str(report_md),
            "policy_version": policy_version,
            "degraded_flags": degraded_flags,
            "stop_reason": stop_reason,
        }
    )

    print(f"RUN_DIR={run_dir}")
    print(f"STOP_REASON={stop_reason}")
    print(f"SUMMARY_PATH={outputs['summary.md']}")

    return 0 if stop_reason != "kill_switch" else 1


if __name__ == "__main__":
    raise SystemExit(main())
