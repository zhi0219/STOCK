from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from pathlib import Path
from typing import Iterable, List, Dict, Sequence
from zoneinfo import ZoneInfo

from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_DATA_PATH = ROOT / "Data" / "quotes.csv"
FIXTURE_DATA_PATH = ROOT / "fixtures" / "data_health" / "clean.csv"
TIMESTAMP_KEYS = ("timestamp", "time", "datetime", "date", "ts")
PRICE_KEYS = ("close", "adj_close", "price", "last", "value")
VOLUME_KEYS = ("volume", "vol", "qty")
MISSINGNESS_RATIO_THRESHOLD = 0.1
MAX_GAP_MULTIPLIER = 3.0
EXTREME_JUMP_THRESHOLD = 0.3
ZERO_VOLUME_RUN_THRESHOLD = 3


@dataclass
class HealthFinding:
    code: str
    message: str
    severity: str


def _load_rows(path: Path) -> List[Dict[str, str]]:
    if path.suffix.lower() == ".jsonl":
        rows: List[Dict[str, str]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append({str(k): str(v) for k, v in item.items()})
        return rows
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "data" in payload:
            payload = payload["data"]
        if isinstance(payload, list):
            return [{str(k): str(v) for k, v in row.items()} for row in payload if isinstance(row, dict)]
        raise ValueError("JSON payload is not a list of objects")
    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            rows.append({str(k): str(v) for k, v in row.items() if k is not None})
    return rows


def _pick_column(columns: Sequence[str], candidates: Sequence[str]) -> str | None:
    normalized = {col.lower(): col for col in columns}
    for key in candidates:
        if key in normalized:
            return normalized[key]
    return None


def _parse_timestamp(raw: str, tz: ZoneInfo) -> tuple[datetime, bool, int | None]:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            raise
    was_naive = parsed.tzinfo is None
    if was_naive:
        parsed = parsed.replace(tzinfo=tz)
    offset = parsed.utcoffset()
    return parsed.astimezone(tz), was_naive, offset


def _resolve_data_path(raw_path: str | None) -> Path:
    if raw_path:
        path = Path(raw_path)
    else:
        path = DEFAULT_DATA_PATH if DEFAULT_DATA_PATH.exists() else FIXTURE_DATA_PATH
    if not path.is_absolute():
        path = ROOT / path
    path = path.expanduser().resolve()
    if path.is_dir():
        candidates = sorted(path.glob("*.csv")) + sorted(path.glob("*.json")) + sorted(path.glob("*.jsonl"))
        if not candidates:
            raise FileNotFoundError(f"No dataset files found in {path}")
        path = candidates[0]
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    return path


def _summarize_zero_volume_run(volumes: List[int]) -> tuple[int, int]:
    max_run = 0
    current = 0
    runs = 0
    for volume in volumes:
        if volume == 0:
            current += 1
            max_run = max(max_run, current)
        else:
            if current >= ZERO_VOLUME_RUN_THRESHOLD:
                runs += 1
            current = 0
    if current >= ZERO_VOLUME_RUN_THRESHOLD:
        runs += 1
    return runs, max_run


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify data health")
    parser.add_argument("--data-path", default=None, help="Dataset path (CSV/JSON/JSONL)")
    parser.add_argument("--artifacts-dir", default="artifacts", help="Artifacts output directory")
    parser.add_argument("--timezone", default="America/New_York", help="Timezone for timestamp parsing")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = artifacts_dir / "data_health_report.json"
    report_txt_path = artifacts_dir / "data_health_report.txt"

    findings: List[HealthFinding] = []
    warnings: List[HealthFinding] = []
    failure_count = 0

    try:
        tz = ZoneInfo(args.timezone)
    except Exception as exc:
        findings.append(HealthFinding("timezone_invalid", f"Invalid timezone: {exc}", "FAIL"))
        tz = None

    try:
        data_path = _resolve_data_path(args.data_path)
    except Exception as exc:
        findings.append(HealthFinding("data_path_invalid", f"Dataset error: {exc}", "FAIL"))
        data_path = None

    rows: List[Dict[str, str]] = []
    if tz and data_path:
        try:
            rows = _load_rows(data_path)
        except Exception as exc:
            findings.append(HealthFinding("data_load_error", f"Failed to load dataset: {exc}", "FAIL"))

    timestamp_values: List[datetime] = []
    duplicates = 0
    monotonic_violations = 0
    parse_errors = 0
    timezone_offsets = set()
    naive_count = 0
    aware_count = 0

    price_values: List[float] = []
    volume_values: List[int] = []

    timestamp_column = None
    price_column = None
    volume_column = None
    if rows:
        columns = list(rows[0].keys())
        timestamp_column = _pick_column(columns, TIMESTAMP_KEYS)
        price_column = _pick_column(columns, PRICE_KEYS)
        volume_column = _pick_column(columns, VOLUME_KEYS)

    if rows and not timestamp_column:
        findings.append(HealthFinding("timestamp_missing", "Timestamp column not found", "FAIL"))

    if rows and not price_column:
        findings.append(HealthFinding("price_missing", "Price/close column not found", "FAIL"))

    if rows and timestamp_column and tz:
        seen = set()
        last_timestamp: datetime | None = None
        for row in rows:
            raw_value = row.get(timestamp_column, "")
            if raw_value is None or raw_value.strip() == "":
                parse_errors += 1
                continue
            try:
                parsed, was_naive, offset = _parse_timestamp(raw_value, tz)
            except Exception:
                parse_errors += 1
                continue
            if was_naive:
                naive_count += 1
            else:
                aware_count += 1
                if offset is not None:
                    timezone_offsets.add(offset)
            timestamp_values.append(parsed)
            if parsed in seen:
                duplicates += 1
            seen.add(parsed)
            if last_timestamp is not None and parsed <= last_timestamp:
                monotonic_violations += 1
            last_timestamp = parsed

    if parse_errors:
        findings.append(
            HealthFinding("timestamp_parse_error", f"Failed to parse {parse_errors} timestamps", "FAIL")
        )
    if duplicates:
        findings.append(HealthFinding("timestamp_duplicates", f"Duplicate timestamps: {duplicates}", "FAIL"))
    if monotonic_violations:
        findings.append(
            HealthFinding(
                "timestamp_monotonicity",
                f"Monotonicity violations: {monotonic_violations}",
                "FAIL",
            )
        )
    if naive_count and aware_count:
        findings.append(
            HealthFinding(
                "timestamp_timezone_mixed",
                "Mixed naive and timezone-aware timestamps detected",
                "FAIL",
            )
        )
    if len(timezone_offsets) > 1:
        findings.append(
            HealthFinding(
                "timestamp_timezone_inconsistent",
                f"Inconsistent timezone offsets: {sorted(timezone_offsets)}",
                "FAIL",
            )
        )

    if rows and price_column:
        for row in rows:
            raw_value = row.get(price_column, "")
            try:
                price = float(raw_value)
                price_values.append(price)
            except Exception:
                continue

    if rows and volume_column:
        for row in rows:
            raw_value = row.get(volume_column, "")
            try:
                volume_values.append(int(float(raw_value)))
            except Exception:
                volume_values.append(0)
    elif rows:
        warnings.append(HealthFinding("volume_missing", "Volume column not found", "WARN"))

    missing_ratio = 0.0
    max_gap_multiplier = 0.0
    expected_rows = len(timestamp_values)
    median_delta = 0.0
    if len(timestamp_values) >= 2 and monotonic_violations == 0:
        deltas = [
            (timestamp_values[idx] - timestamp_values[idx - 1]).total_seconds()
            for idx in range(1, len(timestamp_values))
        ]
        median_delta = median(deltas)
        if median_delta > 0:
            expected_rows = int(round((timestamp_values[-1] - timestamp_values[0]).total_seconds() / median_delta)) + 1
            missing_ratio = max(0.0, (expected_rows - len(timestamp_values)) / expected_rows)
            max_gap_multiplier = max((delta / median_delta for delta in deltas), default=0.0)

            if missing_ratio > MISSINGNESS_RATIO_THRESHOLD:
                findings.append(
                    HealthFinding(
                        "missingness_ratio",
                        f"Missing ratio {missing_ratio:.2%} exceeds {MISSINGNESS_RATIO_THRESHOLD:.0%}",
                        "FAIL",
                    )
                )
            if max_gap_multiplier > MAX_GAP_MULTIPLIER:
                findings.append(
                    HealthFinding(
                        "missingness_gap",
                        f"Max gap {max_gap_multiplier:.2f}x exceeds {MAX_GAP_MULTIPLIER:.1f}x",
                        "FAIL",
                    )
                )

    extreme_jumps = 0
    max_jump = 0.0
    if len(price_values) >= 2:
        prev = price_values[0]
        for price in price_values[1:]:
            if prev == 0:
                prev = price
                continue
            jump = abs(price - prev) / abs(prev)
            max_jump = max(max_jump, jump)
            if jump > EXTREME_JUMP_THRESHOLD:
                extreme_jumps += 1
            prev = price
        if extreme_jumps:
            findings.append(
                HealthFinding(
                    "extreme_jump",
                    f"Extreme jumps detected: {extreme_jumps} (max {max_jump:.2%})",
                    "FAIL",
                )
            )

    zero_volume_runs = 0
    max_zero_volume_run = 0
    if volume_values:
        zero_volume_runs, max_zero_volume_run = _summarize_zero_volume_run(volume_values)
        if zero_volume_runs:
            warnings.append(
                HealthFinding(
                    "zero_volume_runs",
                    f"Zero volume runs detected: {zero_volume_runs} (max run {max_zero_volume_run})",
                    "WARN",
                )
            )

    status = "PASS"
    for finding in findings:
        if finding.severity == "FAIL":
            failure_count += 1
    if failure_count:
        status = "FAIL"

    report = {
        "status": status,
        "data_path": to_repo_relative(data_path) if data_path else None,
        "timezone": args.timezone,
        "row_count": len(rows),
        "expected_rows": expected_rows,
        "timestamp_column": timestamp_column,
        "price_column": price_column,
        "volume_column": volume_column,
        "missing_ratio": round(missing_ratio, 6),
        "max_gap_multiplier": round(max_gap_multiplier, 3),
        "median_delta_seconds": round(median_delta, 3),
        "duplicate_timestamps": duplicates,
        "monotonic_violations": monotonic_violations,
        "timestamp_parse_errors": parse_errors,
        "extreme_jump_count": extreme_jumps,
        "max_jump_ratio": round(max_jump, 6),
        "zero_volume_runs": zero_volume_runs,
        "max_zero_volume_run": max_zero_volume_run,
        "failures": [finding.__dict__ for finding in findings if finding.severity == "FAIL"],
        "warnings": [finding.__dict__ for finding in warnings],
    }

    _write_json(report_json_path, report)
    _write_text(
        report_txt_path,
        [
            f"Status: {status}",
            f"Dataset: {report['data_path']}",
            f"Timezone: {args.timezone}",
            f"Rows: {len(rows)} (expected {expected_rows})",
            f"Missing ratio: {missing_ratio:.2%}",
            f"Max gap multiplier: {max_gap_multiplier:.2f}",
            f"Extreme jumps: {extreme_jumps} (max {max_jump:.2%})",
            f"Failures: {len(report['failures'])}",
            f"Warnings: {len(report['warnings'])}",
        ],
    )

    summary_marker = "|".join(
        [
            "DATA_HEALTH_SUMMARY",
            f"status={status}",
            f"rows={len(rows)}",
            f"failures={len(report['failures'])}",
            f"warnings={len(report['warnings'])}",
            f"report={to_repo_relative(report_json_path)}",
        ]
    )

    print("DATA_HEALTH_START")
    print(summary_marker)
    print("DATA_HEALTH_END")
    return 1 if status == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
