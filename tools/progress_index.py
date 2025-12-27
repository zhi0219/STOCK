from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
DEFAULT_RUNS_ROOT = LOGS_DIR / "train_runs"
DEFAULT_OUTPUT = DEFAULT_RUNS_ROOT / "progress_index.json"

try:
    from tools.wakeup_dashboard import parse_summary_key_fields
except Exception:
    parse_summary_key_fields = None  # type: ignore[assignment]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iter_runs(runs_root: Path) -> Iterable[Path]:
    if not runs_root.exists():
        return []
    run_mtimes: Dict[Path, float] = {}
    patterns = ("**/summary.json", "**/equity_curve.csv", "**/summary.md")
    for pattern in patterns:
        for path in runs_root.glob(pattern):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            run_dir = path.parent
            run_mtimes[run_dir] = max(run_mtimes.get(run_dir, 0.0), mtime)
    runs = sorted(run_mtimes.items(), key=lambda item: item[1], reverse=True)
    return [run_dir for run_dir, _ in runs]


def _load_equity_metrics(
    path: Path, limit: int = 120
) -> Tuple[dict[str, object], list[dict[str, object]], list[str], bool]:
    if not path.exists():
        return {}, [], [], False
    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    parse_error = False
    equities: list[float] = []
    drawdowns: list[float] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                raw_equity = row.get("equity_usd")
                raw_cash = row.get("cash_usd")
                if raw_equity in (None, ""):
                    warnings.append("equity_usd_missing")
                    continue
                try:
                    equity_val = float(raw_equity)
                except (TypeError, ValueError):
                    warnings.append("equity_usd_invalid")
                    continue
                cash_val = 0.0
                if raw_cash not in (None, ""):
                    try:
                        cash_val = float(raw_cash)
                    except (TypeError, ValueError):
                        warnings.append("cash_usd_invalid")
                drawdown_val = None
                raw_dd = row.get("drawdown_pct")
                if raw_dd not in (None, ""):
                    try:
                        drawdown_val = float(raw_dd)
                    except (TypeError, ValueError):
                        warnings.append("drawdown_pct_invalid")
                rows.append(
                    {
                        "ts": row.get("ts_utc"),
                        "equity": equity_val,
                        "cash": cash_val,
                        "drawdown_pct": drawdown_val,
                    }
                )
                equities.append(equity_val)
                if drawdown_val is not None:
                    drawdowns.append(drawdown_val)
                if len(rows) >= limit:
                    break
    except Exception:
        return {}, [], ["equity_curve_read_failed"], True
    if not rows:
        warnings.append("equity_rows_missing")
        return {}, [], warnings, True
    start_equity = equities[0]
    end_equity = equities[-1]
    net_change = end_equity - start_equity
    if drawdowns:
        max_drawdown = max(abs(dd) for dd in drawdowns)
    else:
        max_drawdown = 0.0
        peak = equities[0]
        for eq in equities:
            peak = max(peak, eq)
            if peak <= 0:
                continue
            max_drawdown = max(max_drawdown, (peak - eq) / peak * 100.0)
    return (
        {
            "start_equity": start_equity,
            "end_equity": end_equity,
            "net_change": net_change,
            "max_drawdown": max_drawdown,
        },
        rows,
        warnings,
        parse_error,
    )


def _ensure_safe_output(runs_root: Path, output_path: Path) -> Path:
    resolved_output = output_path.resolve()
    allowed_roots = [runs_root.resolve(), LOGS_DIR.resolve()]
    if not any(str(resolved_output).startswith(str(root)) for root in allowed_roots):
        raise ValueError(f"output must be under {runs_root} or {LOGS_DIR}")
    return resolved_output


def _load_holdings_snapshot(
    holdings_path: Path, limit: int = 6
) -> Tuple[dict[str, object], list[dict[str, object]], bool]:
    if not holdings_path.exists():
        return {}, [], False
    try:
        payload = json.loads(holdings_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, [], True
    if not isinstance(payload, dict):
        return {}, [], True
    positions = payload.get("positions", {}) if isinstance(payload.get("positions"), dict) else {}
    sorted_positions = sorted(
        ((sym, positions.get(sym, 0.0)) for sym in positions),
        key=lambda item: abs(float(item[1] or 0.0)),
        reverse=True,
    )
    preview = [
        {"symbol": str(sym), "qty": float(qty or 0.0)}
        for sym, qty in sorted_positions[:limit]
    ]
    return payload, preview, False


def _summary_fields(summary_path: Path) -> dict[str, object]:
    if not parse_summary_key_fields or not summary_path.exists():
        return {}
    parsed = parse_summary_key_fields(summary_path)
    return {
        "stop_reason": parsed.stop_reason,
        "net_change": parsed.net_change,
        "max_drawdown": parsed.max_drawdown,
        "trades_count": parsed.trades_count,
        "reject_reasons_top3": parsed.reject_reasons_top3,
        "turnover": parsed.turnover,
        "reject_count": parsed.reject_count,
        "gates_triggered": parsed.gates_triggered,
        "raw_preview": parsed.raw_preview,
        "warning": parsed.warning,
    }


def _load_summary_json(summary_path: Path) -> Tuple[dict[str, object], list[str], bool]:
    if not summary_path.exists():
        return {}, [], False
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, ["summary_json_parse_failed"], True
    if not isinstance(payload, dict):
        return {}, ["summary_json_invalid"], True
    required = [
        "schema_version",
        "policy_version",
        "start_equity",
        "end_equity",
        "net_change",
        "max_drawdown",
        "turnover",
        "rejects_count",
        "gates_triggered",
        "stop_reason",
        "timestamps",
        "parse_warnings",
    ]
    missing = [key for key in required if key not in payload]
    warnings = list(payload.get("parse_warnings") or []) if isinstance(payload.get("parse_warnings"), list) else []
    if missing:
        warnings.append(f"summary_json_missing_fields:{','.join(missing)}")
        return payload, warnings, True
    return payload, warnings, False


def _load_judge_summary(judge_path: Path) -> Tuple[dict[str, object], bool]:
    if not judge_path.exists():
        return {}, False
    try:
        payload = json.loads(judge_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, True
    if not isinstance(payload, dict):
        return {}, True
    if "xp" not in payload or "level" not in payload:
        return payload, True
    return payload, False


def _still_writing(run_dir: Path) -> bool:
    for name in ("summary.json.tmp", "holdings.json.tmp", "equity_curve.csv.tmp"):
        if (run_dir / name).exists():
            return True
    for name in ("summary.json", "holdings.json", "equity_curve.csv"):
        path = run_dir / name
        if path.exists():
            try:
                if path.stat().st_size == 0:
                    return True
            except OSError:
                return True
    return False


def build_progress_index(runs_root: Path, max_runs: int = 50) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for run_dir in _iter_runs(runs_root):
        summary_md_path = run_dir / "summary.md"
        summary_json_path = run_dir / "summary.json"
        holdings_path = run_dir / "holdings.json"
        equity_path = run_dir / "equity_curve.csv"
        run_mtime = None
        judge_path = run_dir / "judge_summary.json"
        for path in (summary_json_path, equity_path, summary_md_path):
            if not path.exists():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            run_mtime = max(run_mtime or 0.0, mtime)
        entry = {
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "summary_path": str(summary_md_path) if summary_md_path.exists() else "",
            "summary_json_path": str(summary_json_path) if summary_json_path.exists() else "",
            "holdings_path": str(holdings_path) if holdings_path.exists() else "",
            "mtime": datetime.fromtimestamp(run_mtime or 0.0, tz=timezone.utc).isoformat(),
        }

        has_equity_curve = equity_path.exists()
        has_summary_json = summary_json_path.exists()
        has_holdings_json = holdings_path.exists()
        parse_errors: list[str] = []

        summary_json, summary_warnings, summary_parse_error = _load_summary_json(summary_json_path)
        if summary_parse_error:
            parse_errors.extend(summary_warnings or ["summary_json_parse_error"])

        equity_stats: dict[str, object] = {}
        equity_points: list[dict[str, object]] = []
        equity_warnings: list[str] = []
        if has_equity_curve:
            equity_stats, equity_points, equity_warnings, equity_parse_error = _load_equity_metrics(
                equity_path
            )
            entry["equity_path"] = str(equity_path)
            if equity_stats:
                entry["equity_stats"] = equity_stats
            if equity_points:
                entry["equity_points"] = equity_points
            if equity_parse_error:
                parse_errors.extend(equity_warnings or ["equity_curve_parse_error"])
        holdings_snapshot, holdings_preview, holdings_parse_error = _load_holdings_snapshot(holdings_path)
        if holdings_snapshot:
            entry["holdings_snapshot"] = holdings_snapshot
        if holdings_preview:
            entry["holdings_preview"] = holdings_preview
        if holdings_parse_error:
            parse_errors.append("holdings_json_parse_error")

        judge_summary, judge_parse_error = _load_judge_summary(judge_path)
        if judge_summary:
            entry["judge_summary"] = judge_summary
        if judge_parse_error:
            parse_errors.append("judge_summary_parse_error")

        summary_from_md = _summary_fields(summary_md_path) if summary_md_path.exists() else {}
        summary = {}
        if summary_json:
            summary = summary_json.copy()
        else:
            if summary_from_md:
                summary = summary_from_md
            if equity_stats:
                summary["net_change"] = equity_stats.get("net_change")
                summary["max_drawdown"] = equity_stats.get("max_drawdown")
        if summary:
            entry["summary"] = summary

        still_writing = _still_writing(run_dir)
        missing_reasons: list[str] = []
        if not has_equity_curve:
            missing_reasons.append("equity_curve_missing")
        if not has_summary_json:
            missing_reasons.append("summary_json_missing")
        if not has_holdings_json:
            missing_reasons.append("holdings_json_missing")
        if summary_warnings:
            missing_reasons.extend(summary_warnings)
        if equity_warnings and not parse_errors:
            missing_reasons.extend(equity_warnings)
        if parse_errors:
            missing_reasons.append("parse_error")
        if still_writing:
            missing_reasons.append("still_writing")

        entry.update(
            {
                "has_equity_curve": has_equity_curve,
                "has_summary_json": has_summary_json,
                "has_holdings_json": has_holdings_json,
                "parse_error": bool(parse_errors),
                "still_writing": still_writing,
                "missing_reason": ";".join(missing_reasons) if missing_reasons else "",
            }
        )

        entries.append(entry)
        if len(entries) >= max_runs:
            break

    return {
        "generated_ts": _now(),
        "runs_root": str(runs_root),
        "entries": entries,
    }


def write_progress_index(payload: dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(output_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build progress index for SIM-only runs")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-runs", type=int, default=50)
    args = parser.parse_args(argv)

    status = "PASS"
    message = ""
    payload: dict[str, object] = {"entries": []}
    try:
        safe_output = _ensure_safe_output(args.runs_root, args.output)
        payload = build_progress_index(args.runs_root, max_runs=int(args.max_runs))
        write_progress_index(payload, safe_output)
    except Exception as exc:  # pragma: no cover - fail closed
        status = "FAIL"
        message = str(exc)

    summary = "|".join(
        [
            "PROGRESS_INDEX_SUMMARY",
            f"status={status}",
            f"runs_root={args.runs_root}",
            f"output={args.output}",
            f"runs_found={len(payload['entries']) if status == 'PASS' else 0}",
            f"message={message or 'ok'}",
        ]
    )
    print("PROGRESS_INDEX_START")
    print(summary)
    if status != "PASS":
        print(f"Progress index failed: {message}")
    print("PROGRESS_INDEX_END")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
