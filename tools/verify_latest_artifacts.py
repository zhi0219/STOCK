from __future__ import annotations

import json
from pathlib import Path

from tools.paths import policy_registry_runtime_path

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
LATEST_DIR = RUNS_ROOT / "_latest"
PROGRESS_JUDGE_DIR = RUNS_ROOT / "progress_judge"
SUMMARY_TAG = "LATEST_ARTIFACTS_SUMMARY"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_by_mtime(paths: list[Path]) -> Path | None:
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def _match_run_id(latest_payload: dict, candidate_payload: dict) -> bool:
    return latest_payload.get("run_id") == candidate_payload.get("run_id")


def _check_pointer(
    name: str,
    latest_path: Path,
    candidates: list[Path],
) -> tuple[bool, str]:
    if not latest_path.exists():
        return True, f"{name}:latest_missing_skipped"
    latest_payload = _read_json(latest_path)
    if not latest_payload:
        return False, f"{name}:latest_parse_failed"
    newest = _latest_by_mtime(candidates)
    if not newest:
        return True, f"{name}:no_versioned_candidates"
    newest_payload = _read_json(newest)
    if not newest_payload:
        return False, f"{name}:newest_parse_failed"
    if not _match_run_id(latest_payload, newest_payload):
        return False, f"{name}:run_id_mismatch latest={latest_payload.get('run_id')} newest={newest_payload.get('run_id')}"
    return True, f"{name}:ok"


def _check_policy_history(latest_path: Path, registry_path: Path) -> tuple[bool, str]:
    if not latest_path.exists():
        return True, "policy_history:latest_missing_skipped"
    latest_payload = _read_json(latest_path)
    if not latest_payload:
        return False, "policy_history:latest_parse_failed"
    registry = _read_json(registry_path)
    history = registry.get("history", []) if isinstance(registry.get("history"), list) else []
    if not history:
        return True, "policy_history:registry_empty"
    last_entry = history[-1] if isinstance(history[-1], dict) else {}
    if not last_entry:
        return True, "policy_history:registry_last_empty"
    if latest_payload.get("policy_version") != last_entry.get("policy_version"):
        return False, "policy_history:policy_version_mismatch"
    return True, "policy_history:ok"


def main() -> int:
    status = "PASS"
    details: list[str] = []

    checks = [
        _check_pointer(
            "tournament",
            LATEST_DIR / "tournament_latest.json",
            list(RUNS_ROOT.glob("**/tournament.json")),
        ),
        _check_pointer(
            "promotion_decision",
            LATEST_DIR / "promotion_decision_latest.json",
            list(RUNS_ROOT.glob("**/promotion_decision.json")),
        ),
        _check_pointer(
            "progress_judge",
            LATEST_DIR / "progress_judge_latest.json",
            list(PROGRESS_JUDGE_DIR.glob("progress_judge_*.json")),
        ),
        _check_policy_history(
            LATEST_DIR / "policy_history_latest.json",
            policy_registry_runtime_path(),
        ),
    ]

    for ok, detail in checks:
        details.append(detail)
        if not ok:
            status = "FAIL"

    summary = "|".join(
        [
            SUMMARY_TAG,
            f"status={status}",
            f"details={';'.join(details)}",
        ]
    )
    print(summary)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
