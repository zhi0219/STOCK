from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
DEFAULT_RUNS_ROOT = LOGS_DIR / "train_runs"
SUMMARY_TAG = "PROGRESS_JUDGE_SUMMARY"
KILL_SWITCH_ENV = "PR11_KILL_SWITCH"


def _summary_line(status: str, runs_root: Path, runs_scanned: int, issues: list[str]) -> str:
    reason = ";".join(issues) if issues else "ok"
    return "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"runs_root={runs_root}",
            f"runs_scanned={runs_scanned}",
            f"issues={len(issues)}",
            f"reason={reason}",
        ]
    )


def _validate_runs_root(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"runs_root not found: {resolved}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"runs_root is not a directory: {resolved}")
    return resolved


def _iter_runs(runs_root: Path) -> list[tuple[Path, Path]]:
    runs: list[tuple[Path, Path]] = []
    for summary in runs_root.glob("**/summary.md"):
        run_dir = summary.parent
        runs.append((run_dir, summary))
    runs.sort(key=lambda pair: pair[1].stat().st_mtime if pair[1].exists() else 0, reverse=True)
    return runs


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge SIM-only training progress runs")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    args = parser.parse_args(argv)

    issues: list[str] = []
    try:
        if os.environ.get(KILL_SWITCH_ENV):
            issues.append("kill_switch_engaged")
            raise RuntimeError("Kill switch active")

        runs_root = _validate_runs_root(args.runs_root)
        runs = _iter_runs(runs_root)
        if not runs:
            issues.append("no_runs_found")
            raise RuntimeError("No runs available for judging")

        for run_dir, summary_path in runs:
            issues.extend(_scan_run(run_dir, summary_path))
    except Exception as exc:  # pragma: no cover - defensive fail closed
        if "kill_switch_engaged" not in issues:
            issues.append(str(exc))

    status = "PASS" if not issues else "FAIL"
    runs_scanned = 0
    try:
        runs_scanned = len(_iter_runs(_validate_runs_root(args.runs_root)))
    except Exception:
        runs_scanned = 0

    summary = _summary_line(status, args.runs_root, runs_scanned, issues)
    print(summary)
    if issues:
        print("DETAILS:")
        for issue in issues:
            print(f"- {issue}")
    else:
        print("All inspected runs are SIM-only and well-formed.")
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
