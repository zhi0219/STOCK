from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.paths import logs_dir
from tools.xp_model import compute_xp_snapshot

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = logs_dir()
RUNS_ROOT = LOGS_DIR / "train_runs"
ARTIFACTS_DIR = ROOT / "artifacts"
DEFAULT_ARTIFACTS_OUTPUT = ARTIFACTS_DIR / "xp_snapshot.json"
def _xp_dir(runs_root: Path) -> Path:
    return runs_root / "progress_xp"


def _xp_latest_path(runs_root: Path) -> Path:
    return _xp_dir(runs_root) / "xp_snapshot_latest.json"


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _safe_read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                events.append(payload)
    except Exception:
        return None
    return events


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _validate_runs_root(path: Path) -> Path:
    resolved = path.resolve()
    if not str(resolved).startswith(str(RUNS_ROOT.resolve())):
        raise ValueError("runs_root must be under Logs/train_runs")
    return resolved


def write_xp_snapshot(
    *,
    runs_root: Path = RUNS_ROOT,
    artifacts_output: Path | None = DEFAULT_ARTIFACTS_OUTPUT,
) -> dict[str, Path]:
    runs_root = _validate_runs_root(runs_root)
    latest_dir = runs_root / "_latest"
    tournament_path = latest_dir / "tournament_result_latest.json"
    judge_path = latest_dir / "judge_result_latest.json"
    promotion_path = latest_dir / "promotion_decision_latest.json"
    promotion_history_path = latest_dir / "promotion_history_latest.json"

    doctor_report_path = ARTIFACTS_DIR / "doctor_report.json"
    repo_hygiene_path = ARTIFACTS_DIR / "repo_hygiene.json"

    tournament_payload = _safe_read_json(tournament_path)
    judge_payload = _safe_read_json(judge_path)
    promotion_payload = _safe_read_json(promotion_path)
    promotion_history_payload = _safe_read_json(promotion_history_path)
    doctor_report_payload = _safe_read_json(doctor_report_path)
    repo_hygiene_payload = _safe_read_json(repo_hygiene_path)

    history_events = None
    history_jsonl_path = None
    if promotion_history_payload and isinstance(promotion_history_payload, dict):
        history_path_raw = promotion_history_payload.get("history_path")
        if isinstance(history_path_raw, str) and history_path_raw:
            history_jsonl_path = Path(history_path_raw)
            history_events = _safe_read_jsonl(history_jsonl_path)

    run_id = None
    for payload in (judge_payload, tournament_payload, promotion_payload):
        if isinstance(payload, dict) and payload.get("run_id"):
            run_id = str(payload.get("run_id"))
            break

    created_utc = _now_iso()
    evidence_paths = {
        "tournament": tournament_path,
        "judge": judge_path,
        "promotion": promotion_path,
        "promotion_history": promotion_history_path,
        "promotion_history_jsonl": history_jsonl_path,
        "doctor_report": doctor_report_path,
        "repo_hygiene": repo_hygiene_path,
    }

    snapshot = compute_xp_snapshot(
        tournament=tournament_payload,
        judge=judge_payload,
        promotion=promotion_payload,
        promotion_history=promotion_history_payload,
        promotion_history_events=history_events,
        doctor_report=doctor_report_payload,
        repo_hygiene=repo_hygiene_payload,
        evidence_paths=evidence_paths,
        created_utc=created_utc,
        run_id=run_id,
    )

    versioned_name = f"xp_snapshot_{_now_ts()}.json"
    xp_dir = _xp_dir(runs_root)
    versioned_path = xp_dir / versioned_name
    _atomic_write_json(versioned_path, snapshot)
    _atomic_write_json(_xp_latest_path(runs_root), snapshot)

    if artifacts_output is not None:
        _atomic_write_json(artifacts_output, snapshot)

    return {
        "snapshot": versioned_path,
        "latest": _xp_latest_path(runs_root),
        "artifacts_output": artifacts_output or versioned_path,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write XP snapshot artifact (SIM-only)")
    parser.add_argument("--runs-root", default=str(RUNS_ROOT), help="Runs root under Logs/train_runs")
    parser.add_argument("--artifacts-output", default=str(DEFAULT_ARTIFACTS_OUTPUT))
    parser.add_argument("--no-artifacts-output", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = (ROOT / runs_root).resolve()
    artifacts_output = None if args.no_artifacts_output else Path(args.artifacts_output)
    write_xp_snapshot(runs_root=runs_root, artifacts_output=artifacts_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
