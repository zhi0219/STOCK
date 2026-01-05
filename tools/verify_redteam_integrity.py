from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_FIXTURES_DIR = ROOT / "fixtures" / "redteam_integrity"
TIMESTAMP_KEYS = ("timestamp", "time", "datetime", "date", "ts")
LABEL_KEYS = ("label", "target", "y", "return", "label_return")
LABEL_TIMESTAMP_KEYS = ("label_timestamp", "target_timestamp", "label_ts")
SYMBOL_KEYS = ("symbol", "ticker", "asset", "id")
FEATURE_ALLOWLIST_PREFIXES = ("feature_", "alpha_", "signal_", "indicator_")
FEATURE_DENYLIST_TOKENS = ("future", "ahead", "lookahead", "t+1", "next_return", "lead")
SURVIVORSHIP_MIN_COVERAGE = 0.8


@dataclass
class CaseResult:
    name: str
    expected: str
    status: str
    reasons: list[str]
    fixture_path: Path


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_rows(path: Path) -> List[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            rows.append({str(k): str(v) for k, v in row.items() if k is not None})
    return rows


def _pick_column(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    normalized = {col.lower(): col for col in columns}
    for key in candidates:
        if key in normalized:
            return normalized[key]
    return None


def _parse_timestamp(raw: str) -> datetime:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _normalized_value(row: dict[str, str], column: str) -> str:
    return str(row.get(column, "") or "").strip()


def _detect_lookahead_features(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["empty_dataset"]
    columns = list(rows[0].keys())
    timestamp_column = _pick_column(columns, TIMESTAMP_KEYS)
    label_column = _pick_column(columns, LABEL_KEYS)
    label_ts_column = _pick_column(columns, LABEL_TIMESTAMP_KEYS)
    symbol_column = _pick_column(columns, SYMBOL_KEYS)
    feature_columns = [
        col
        for col in columns
        if col not in {timestamp_column, label_column, label_ts_column, symbol_column}
    ]

    reasons: list[str] = []
    for col in feature_columns:
        lower = col.lower()
        if any(token in lower for token in FEATURE_DENYLIST_TOKENS):
            reasons.append(f"denylist_feature_name:{col}")
        if (
            any(token in lower for token in ("return", "label", "target"))
            and not lower.startswith(FEATURE_ALLOWLIST_PREFIXES)
        ):
            reasons.append(f"feature_name_not_allowlisted:{col}")

    if not label_column:
        return reasons

    labels = [_normalized_value(row, label_column) for row in rows]
    for col in feature_columns:
        comparisons = 0
        matches = 0
        for idx in range(len(rows) - 1):
            feature_value = _normalized_value(rows[idx], col)
            future_label = labels[idx + 1]
            if feature_value == "" or future_label == "":
                continue
            comparisons += 1
            if feature_value == future_label:
                matches += 1
        if comparisons >= 3 and matches / comparisons >= 0.9:
            reasons.append(f"feature_matches_future_label:{col}")
    return reasons


def _detect_label_misalignment(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["empty_dataset"]
    columns = list(rows[0].keys())
    timestamp_column = _pick_column(columns, TIMESTAMP_KEYS)
    label_ts_column = _pick_column(columns, LABEL_TIMESTAMP_KEYS)
    if not timestamp_column or not label_ts_column:
        return []
    mismatches = 0
    for row in rows:
        ts_raw = _normalized_value(row, timestamp_column)
        label_ts_raw = _normalized_value(row, label_ts_column)
        if not ts_raw or not label_ts_raw:
            continue
        if _parse_timestamp(ts_raw) != _parse_timestamp(label_ts_raw):
            mismatches += 1
    if mismatches:
        return [f"label_timestamp_mismatch:{mismatches}"]
    return []


def _detect_non_monotonic_time(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["empty_dataset"]
    columns = list(rows[0].keys())
    timestamp_column = _pick_column(columns, TIMESTAMP_KEYS)
    if not timestamp_column:
        return ["timestamp_missing"]
    previous: datetime | None = None
    for row in rows:
        ts_raw = _normalized_value(row, timestamp_column)
        if not ts_raw:
            continue
        current = _parse_timestamp(ts_raw)
        if previous is not None and current < previous:
            return ["timestamp_non_monotonic"]
        previous = current
    return []


def _detect_survivorship_bias(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["empty_dataset"]
    columns = list(rows[0].keys())
    timestamp_column = _pick_column(columns, TIMESTAMP_KEYS)
    symbol_column = _pick_column(columns, SYMBOL_KEYS)
    if not timestamp_column or not symbol_column:
        return []
    timestamps = [
        _parse_timestamp(_normalized_value(row, timestamp_column))
        for row in rows
        if _normalized_value(row, timestamp_column)
    ]
    if not timestamps:
        return ["timestamp_missing"]
    unique_timestamps = sorted({ts for ts in timestamps})
    expected_count = len(unique_timestamps)
    if expected_count == 0:
        return ["timestamp_missing"]

    reasons: list[str] = []
    symbols = sorted({row[symbol_column] for row in rows if row.get(symbol_column)})
    global_start = min(unique_timestamps)
    global_end = max(unique_timestamps)
    for symbol in symbols:
        symbol_timestamps = sorted(
            {
                _parse_timestamp(_normalized_value(row, timestamp_column))
                for row in rows
                if row.get(symbol_column) == symbol and _normalized_value(row, timestamp_column)
            }
        )
        if not symbol_timestamps:
            reasons.append(f"symbol_missing_all_rows:{symbol}")
            continue
        coverage = len(symbol_timestamps) / expected_count
        if coverage < SURVIVORSHIP_MIN_COVERAGE:
            reasons.append(f"symbol_low_coverage:{symbol}:{coverage:.2f}")
        if symbol_timestamps[0] > global_start or symbol_timestamps[-1] < global_end:
            reasons.append(f"symbol_incomplete_range:{symbol}")
    return reasons


def _load_trial_budget(fixtures_dir: Path) -> tuple[dict[str, int] | None, list[str]]:
    budget_path = fixtures_dir / "trial_budget.json"
    if not budget_path.exists():
        return None, ["trial_budget_missing"]
    payload = json.loads(budget_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None, ["trial_budget_invalid"]
    trial_count = payload.get("trial_count")
    candidate_count = payload.get("candidate_count")
    if not isinstance(trial_count, int) or not isinstance(candidate_count, int):
        return None, ["trial_budget_invalid"]
    return {"trial_count": trial_count, "candidate_count": candidate_count}, []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify red-team integrity scenarios (fail-closed).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--fixtures-dir", default=str(DEFAULT_FIXTURES_DIR), help="Red-team fixtures directory")
    parser.add_argument("--artifacts-dir", default="artifacts", help="Artifacts output directory")
    return parser.parse_args(argv)


def _run_case(name: str, fixture_path: Path, expected: str) -> CaseResult:
    try:
        rows = _load_rows(fixture_path)
    except Exception as exc:
        return CaseResult(
            name=name,
            expected=expected,
            status="FAIL",
            reasons=[f"fixture_load_error:{exc}"],
            fixture_path=fixture_path,
        )
    reasons: list[str] = []
    reasons.extend(_detect_lookahead_features(rows))
    reasons.extend(_detect_label_misalignment(rows))
    reasons.extend(_detect_non_monotonic_time(rows))
    reasons.extend(_detect_survivorship_bias(rows))
    status = "FAIL" if reasons else "PASS"
    return CaseResult(
        name=name,
        expected=expected,
        status=status,
        reasons=sorted(set(reasons)),
        fixture_path=fixture_path,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fixtures_dir = Path(args.fixtures_dir).expanduser().resolve()
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    report_json_path = artifacts_dir / "redteam_report.json"
    report_txt_path = artifacts_dir / "redteam_report.txt"

    print("REDTEAM_START")

    cases = [
        ("control", fixtures_dir / "control.csv", "PASS"),
        ("lookahead_feature_injection", fixtures_dir / "lookahead_feature.csv", "FAIL"),
        ("label_misalignment", fixtures_dir / "label_misalignment.csv", "FAIL"),
        ("shuffled_time_order", fixtures_dir / "shuffled_time.csv", "FAIL"),
        ("survivorship_bias", fixtures_dir / "survivorship_bias.csv", "FAIL"),
    ]

    case_results: list[CaseResult] = []
    unexpected_passes = 0
    unexpected_fails = 0
    for name, fixture_path, expected in cases:
        result = _run_case(name, fixture_path, expected)
        case_results.append(result)
        if result.status != result.expected:
            if result.status == "PASS":
                unexpected_passes += 1
            else:
                unexpected_fails += 1
        detail = ",".join(result.reasons) if result.reasons else "ok"
        print(
            "|".join(
                [
                    "REDTEAM_CASE",
                    f"name={result.name}",
                    f"status={result.status}",
                    f"expected={result.expected}",
                    f"detail={detail}",
                ]
            )
        )
        _write_text(
            artifacts_dir / f"redteam_{result.name}.txt",
            [
                f"name: {result.name}",
                f"expected: {result.expected}",
                f"status: {result.status}",
                f"fixture: {to_repo_relative(result.fixture_path)}",
                f"reasons: {detail}",
            ],
        )

    trial_budget, trial_budget_issues = _load_trial_budget(fixtures_dir)
    status = "PASS"
    summary_reasons: list[str] = []
    if unexpected_passes or unexpected_fails:
        status = "FAIL"
        if unexpected_passes:
            summary_reasons.append(f"unexpected_passes:{unexpected_passes}")
        if unexpected_fails:
            summary_reasons.append(f"unexpected_failures:{unexpected_fails}")
    if trial_budget_issues:
        status = "FAIL"
        summary_reasons.extend(trial_budget_issues)

    report_payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "summary_reasons": summary_reasons,
        "unexpected_passes": unexpected_passes,
        "unexpected_failures": unexpected_fails,
        "trial_budget": trial_budget,
        "cases": [
            {
                "name": result.name,
                "expected": result.expected,
                "status": result.status,
                "reasons": result.reasons,
                "fixture": to_repo_relative(result.fixture_path),
            }
            for result in case_results
        ],
    }
    _write_json(report_json_path, report_payload)
    _write_text(
        report_txt_path,
        [
            "Red-team integrity gate",
            f"Status: {status}",
            f"Unexpected passes: {unexpected_passes}",
            f"Unexpected failures: {unexpected_fails}",
            f"Trial budget: {trial_budget or 'missing'}",
            "Cases:",
            *[
                f"- {result.name}: status={result.status} expected={result.expected} reasons={','.join(result.reasons) or 'ok'}"
                for result in case_results
            ],
        ],
    )

    summary_detail = ",".join(summary_reasons) if summary_reasons else "ok"
    print(
        "|".join(
            [
                "REDTEAM_SUMMARY",
                f"status={status}",
                f"cases={len(case_results)}",
                f"unexpected_passes={unexpected_passes}",
                f"unexpected_failures={unexpected_fails}",
                f"detail={summary_detail}",
                f"report={report_json_path}",
            ]
        )
    )
    print("REDTEAM_END")
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
