from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tools.experiment_ledger import DEFAULT_BASELINES, resolve_latest_ledger_path
from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_BUDGET_PATH = ROOT / "fixtures" / "multiple_testing_control" / "trial_budget.json"
REQUIRED_FIELDS = (
    "run_id",
    "timestamp",
    "candidate_count",
    "trial_count",
    "baselines_used",
    "window_config_hash",
    "code_hash",
)


@dataclass
class CaseResult:
    name: str
    status: str
    detail: str


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _emit_marker(text: str) -> None:
    print(text)


def _load_budget(path: Path) -> tuple[dict[str, int] | None, list[str]]:
    if not path.exists():
        return None, ["trial_budget_missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, ["trial_budget_invalid"]
    if not isinstance(payload, dict):
        return None, ["trial_budget_invalid"]
    trial_count = payload.get("trial_count")
    candidate_count = payload.get("candidate_count")
    if not isinstance(trial_count, int) or not isinstance(candidate_count, int):
        return None, ["trial_budget_invalid"]
    return {"trial_count": trial_count, "candidate_count": candidate_count}, []


def _load_ledger(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    if not path.exists():
        return [], ["ledger_missing"]
    entries: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            return [], ["ledger_invalid_json"]
        if not isinstance(payload, dict):
            return [], ["ledger_invalid_payload"]
        entries.append(payload)
    if not entries:
        return [], ["ledger_empty"]
    return entries, []


def _normalize_baseline(label: str) -> str:
    cleaned = "".join(ch for ch in label.lower() if ch.isalnum())
    return cleaned


def _required_baselines_present(baselines: Iterable[str]) -> tuple[bool, list[str]]:
    normalized = {_normalize_baseline(label) for label in baselines if label}
    required = {
        "donothing": "DoNothing",
        "buyhold": "Buy&Hold",
        "simplemomentum": "SimpleMomentum",
    }
    required_keys = {"donothing", "buyhold", "simplemomentum"}
    missing = [required[key] for key in required_keys if key not in normalized]
    return not missing, missing


def _validate_entry(entry: dict[str, object]) -> list[str]:
    missing = [field for field in REQUIRED_FIELDS if field not in entry]
    errors: list[str] = []
    if missing:
        errors.extend([f"missing_field:{field}" for field in missing])
        return errors
    if not isinstance(entry.get("candidate_count"), int):
        errors.append("candidate_count_not_int")
    if not isinstance(entry.get("trial_count"), int):
        errors.append("trial_count_not_int")
    baselines = entry.get("baselines_used")
    if not isinstance(baselines, list):
        errors.append("baselines_used_not_list")
    else:
        ok, missing_baselines = _required_baselines_present([str(b) for b in baselines])
        if not ok:
            errors.append(f"missing_baselines:{','.join(missing_baselines)}")
    timestamp = str(entry.get("timestamp") or "")
    try:
        normalized = timestamp.replace("Z", "+00:00")
        datetime.fromisoformat(normalized)
    except Exception:
        errors.append("timestamp_invalid")
    for field in ("window_config_hash", "code_hash"):
        if not isinstance(entry.get(field), str) or not str(entry.get(field)).strip():
            errors.append(f"{field}_missing")
    return errors


def _penalty(trial_count: int, budget: int) -> float:
    if budget <= 0:
        return float(trial_count)
    return round(trial_count / budget, 4)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify multiple-testing governance controls (fail-closed).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--artifacts-dir", default="artifacts", help="Artifacts output directory")
    parser.add_argument(
        "--budget-path",
        default=str(DEFAULT_BUDGET_PATH),
        help="Trial budget JSON path",
    )
    parser.add_argument(
        "--ledger-path",
        default=None,
        help="Override experiment ledger JSONL path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    ledger_path = Path(args.ledger_path) if args.ledger_path else resolve_latest_ledger_path(
        artifacts_dir, fallback=artifacts_dir / "experiment_ledger.jsonl"
    )
    budget_path = Path(args.budget_path)

    summary_path = artifacts_dir / "experiment_ledger_summary.json"

    _emit_marker("MULTITEST_START")

    status = "PASS"
    reasons: list[str] = []
    case_results: list[CaseResult] = []

    entries, ledger_issues = _load_ledger(ledger_path)
    if ledger_issues:
        status = "FAIL"
        reasons.extend(ledger_issues)
        case_results.append(CaseResult("ledger", "FAIL", ",".join(ledger_issues)))
    else:
        case_results.append(CaseResult("ledger", "PASS", "ok"))

    budget, budget_issues = _load_budget(budget_path)
    if budget_issues:
        status = "FAIL"
        reasons.extend(budget_issues)
        case_results.append(CaseResult("budget", "FAIL", ",".join(budget_issues)))
    else:
        case_results.append(CaseResult("budget", "PASS", "ok"))

    latest_entry = entries[-1] if entries else {}
    entry_errors = _validate_entry(latest_entry) if entries else []
    if entry_errors:
        status = "FAIL"
        reasons.extend(entry_errors)
        case_results.append(CaseResult("ledger_fields", "FAIL", ",".join(entry_errors)))
    elif entries:
        case_results.append(CaseResult("ledger_fields", "PASS", "ok"))

    trial_count = int(latest_entry.get("trial_count") or 0) if isinstance(latest_entry, dict) else 0
    candidate_count = int(latest_entry.get("candidate_count") or 0) if isinstance(latest_entry, dict) else 0
    requested_trial_count = int(latest_entry.get("requested_trial_count") or trial_count) if isinstance(
        latest_entry, dict
    ) else trial_count
    requested_candidate_count = int(
        latest_entry.get("requested_candidate_count") or candidate_count
    ) if isinstance(latest_entry, dict) else candidate_count
    enforced_trial_count = int(latest_entry.get("enforced_trial_count") or trial_count) if isinstance(
        latest_entry, dict
    ) else trial_count
    enforced_candidate_count = int(
        latest_entry.get("enforced_candidate_count") or candidate_count
    ) if isinstance(latest_entry, dict) else candidate_count
    baselines_used = latest_entry.get("baselines_used") if isinstance(latest_entry, dict) else []
    override_flag = bool(latest_entry.get("trial_budget_override")) if isinstance(latest_entry, dict) else False

    budget_trial_count = budget.get("trial_count") if isinstance(budget, dict) else None
    budget_candidate_count = budget.get("candidate_count") if isinstance(budget, dict) else None
    penalty = None
    if isinstance(budget_trial_count, int):
        penalty = _penalty(trial_count, budget_trial_count)
        if trial_count > budget_trial_count and not override_flag:
            status = "FAIL"
            reasons.append("trial_budget_exceeded")
            case_results.append(
                CaseResult(
                    "trial_budget",
                    "FAIL",
                    f"trial_count={trial_count} budget={budget_trial_count}",
                )
            )
        else:
            case_results.append(
                CaseResult(
                    "trial_budget",
                    "PASS",
                    f"trial_count={trial_count} budget={budget_trial_count}",
                )
            )

    if isinstance(budget_candidate_count, int):
        if candidate_count > budget_candidate_count and not override_flag:
            status = "FAIL"
            reasons.append("candidate_budget_exceeded")
            case_results.append(
                CaseResult(
                    "candidate_budget",
                    "FAIL",
                    f"candidate_count={candidate_count} budget={budget_candidate_count}",
                )
            )
        else:
            case_results.append(
                CaseResult(
                    "candidate_budget",
                    "PASS",
                    f"candidate_count={candidate_count} budget={budget_candidate_count}",
                )
            )

    for case in case_results:
        _emit_marker(
            "|".join(
                [
                    "MULTITEST_CASE",
                    f"name={case.name}",
                    f"status={case.status}",
                    f"detail={case.detail}",
                ]
            )
        )

    summary_payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "reasons": reasons,
        "ledger_path": to_repo_relative(ledger_path) if ledger_path.exists() else str(ledger_path),
        "budget_path": to_repo_relative(budget_path) if budget_path.exists() else str(budget_path),
        "latest_entry": latest_entry,
        "trial_count": trial_count,
        "candidate_count": candidate_count,
        "requested_trial_count": requested_trial_count,
        "requested_candidate_count": requested_candidate_count,
        "enforced_trial_count": enforced_trial_count,
        "enforced_candidate_count": enforced_candidate_count,
        "baselines_used": baselines_used,
        "required_baselines": DEFAULT_BASELINES,
        "trial_budget": budget,
        "search_scale_penalty": penalty,
    }
    _write_json(summary_path, summary_payload)

    summary_detail = ",".join(reasons) if reasons else "ok"
    _emit_marker(
        "|".join(
            [
                "MULTITEST_SUMMARY",
                f"status={status}",
                f"trial_count={trial_count}",
                f"candidate_count={candidate_count}",
                f"requested_trial_count={requested_trial_count}",
                f"requested_candidate_count={requested_candidate_count}",
                f"enforced_trial_count={enforced_trial_count}",
                f"enforced_candidate_count={enforced_candidate_count}",
                f"penalty={penalty if penalty is not None else 'n/a'}",
                f"detail={summary_detail}",
                f"report={to_repo_relative(summary_path)}",
            ]
        )
    )
    _emit_marker("MULTITEST_END")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
