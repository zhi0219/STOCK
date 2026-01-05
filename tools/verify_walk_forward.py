from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from tools.paths import repo_root, to_repo_relative
from tools.walk_forward_eval import (
    BASELINE_POLICIES,
    STRATEGY_POLICIES,
    build_windows,
    evaluate_walk_forward,
    _load_bars,
    _resolve_data_path,
)

ROOT = repo_root()
DEFAULT_TIMEZONE = "America/New_York"


@dataclass
class GateResult:
    status: str
    reasons: list[str]


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _hash_files(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _emit_marker(text: str) -> None:
    print(text)


def _format_range(rng: tuple[int, int] | None) -> str:
    if rng is None:
        return "none"
    return f"{rng[0]}-{rng[1]}"


def _build_window_rows(windows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for window in windows:
        row = {
            "window_index": window["window_index"],
            "train_start": window["train_range"][0],
            "train_end": window["train_range"][1],
            "gap_start": window["gap_range"][0] if window["gap_range"] else None,
            "gap_end": window["gap_range"][1] if window["gap_range"] else None,
            "test_start": window["test_range"][0],
            "test_end": window["test_range"][1],
            "train_start_ts": window["train_start"],
            "train_end_ts": window["train_end"],
            "test_start_ts": window["test_start"],
            "test_end_ts": window["test_end"],
            "strategy_return_pct": window["strategy"]["return_pct"],
        }
        for baseline in BASELINE_POLICIES:
            row[f"baseline_{baseline}_return_pct"] = window["baselines"][baseline]
        rows.append(row)
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify deterministic walk-forward evaluation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--artifacts-dir", default="artifacts", help="Artifacts output directory")
    parser.add_argument("--input", default=None, help="Input OHLCV CSV path")
    parser.add_argument("--train-size", type=int, default=5, help="Training window size (bars)")
    parser.add_argument("--gap-size", type=int, default=2, help="Embargo/gap size (bars)")
    parser.add_argument("--test-size", type=int, default=4, help="Test window size (bars)")
    parser.add_argument("--step-size", type=int, default=4, help="Rolling step size (bars)")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="Timezone for timestamps")
    parser.add_argument("--strategy", default="placeholder", help="Strategy policy name")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    report_json_path = artifacts_dir / "walk_forward_report.json"
    report_txt_path = artifacts_dir / "walk_forward_report.txt"
    windows_path = artifacts_dir / "walk_forward_windows.csv"

    _emit_marker("WALK_FORWARD_START")

    reasons: list[str] = []
    status = "PASS"

    if args.gap_size <= 0:
        status = "FAIL"
        reasons.append("gap_required")

    if args.strategy not in STRATEGY_POLICIES:
        status = "FAIL"
        reasons.append("unknown_strategy")

    tz = ZoneInfo(args.timezone)
    data_path = _resolve_data_path(args.input)
    bars = _load_bars(data_path, tz)

    window_specs = build_windows(
        len(bars),
        train_size=args.train_size,
        gap_size=args.gap_size,
        test_size=args.test_size,
        step_size=args.step_size,
    )

    report = evaluate_walk_forward(bars, window_specs, args.strategy)
    windows = report["windows"]

    if not windows:
        status = "FAIL"
        reasons.append("no_windows")

    if not report.get("baselines"):
        status = "FAIL"
        reasons.append("missing_baselines")

    for window in windows:
        marker = "|".join(
            [
                "WALK_FORWARD_WINDOW",
                f"idx={window['window_index']}",
                f"train={_format_range(window['train_range'])}",
                f"gap={_format_range(window['gap_range'])}",
                f"test={_format_range(window['test_range'])}",
                f"status={status}",
            ]
        )
        _emit_marker(marker)

    data_hash = _hash_bytes(data_path.read_bytes())
    code_hash = _hash_files(
        [
            ROOT / "tools" / "walk_forward_eval.py",
            ROOT / "tools" / "verify_walk_forward.py",
        ]
    )

    report_payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "reasons": reasons,
        "input_path": to_repo_relative(data_path),
        "timezone": args.timezone,
        "window_config": {
            "train_size": args.train_size,
            "gap_size": args.gap_size,
            "test_size": args.test_size,
            "step_size": args.step_size,
        },
        "strategy": report["strategy"],
        "baselines": report["baselines"],
        "summary": report["summary"],
        "window_count": len(windows),
        "data_hash": data_hash,
        "code_hash": code_hash,
        "windows_path": to_repo_relative(windows_path),
        "report_path": to_repo_relative(report_json_path),
    }

    _write_json(report_json_path, report_payload)
    _write_csv(windows_path, _build_window_rows(windows))

    text_lines = [
        f"Status: {status}",
        f"Windows: {len(windows)}",
        f"Strategy: {report['strategy']}",
        f"Baselines: {', '.join(report['baselines'])}",
        f"Data hash: {data_hash}",
        f"Code hash: {code_hash}",
    ]
    if reasons:
        text_lines.append(f"Reasons: {', '.join(reasons)}")
    _write_text(report_txt_path, text_lines)

    summary_marker = "|".join(
        [
            "WALK_FORWARD_SUMMARY",
            f"status={status}",
            f"windows={len(windows)}",
            f"baselines={','.join(report['baselines'])}",
            f"notes={','.join(reasons) if reasons else 'ok'}",
        ]
    )
    _emit_marker(summary_marker)
    _emit_marker("WALK_FORWARD_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
