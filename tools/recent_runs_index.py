from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.fs_atomic import atomic_write_json
from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
LATEST_DIR = RUNS_ROOT / "_latest"
DEFAULT_INDEX_PATH = RUNS_ROOT / "recent_runs_index.json"
DEFAULT_LATEST_PATH = LATEST_DIR / "recent_runs_index_latest.json"
POLICY_PATH = ROOT / "Data" / "retention_policy.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _now().isoformat()


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_max_runs(default: int = 100) -> int:
    env_override = os.environ.get("RETENTION_RECENT_INDEX_MAX_RUNS")
    if env_override:
        try:
            value = int(env_override)
            return max(1, value)
        except ValueError:
            return default
    policy = _safe_read_json(POLICY_PATH)
    value = policy.get("recent_index_max_runs")
    if isinstance(value, int) and value > 0:
        return value
    return default


def _run_dir_mtime(path: Path) -> float:
    marker = path / "run_complete.json"
    if marker.exists():
        try:
            return marker.stat().st_mtime
        except OSError:
            return 0.0
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _collect_run_dirs(runs_root: Path) -> list[Path]:
    if not runs_root.exists():
        return []
    run_dirs: list[Path] = []
    for child in runs_root.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        run_dirs.append(child)
    return sorted(run_dirs, key=_run_dir_mtime, reverse=True)


def _run_id_from_dir(run_dir: Path) -> tuple[str, str | None]:
    run_complete = _safe_read_json(run_dir / "run_complete.json")
    run_id = run_complete.get("run_id")
    ts_utc = run_complete.get("ts_utc") or run_complete.get("created_utc")
    if run_id:
        return str(run_id), str(ts_utc) if ts_utc else None
    return run_dir.name, str(ts_utc) if ts_utc else None


def _find_replay_index(run_dir: Path) -> Path | None:
    latest = run_dir / "_latest" / "replay_index_latest.json"
    if latest.exists():
        return latest
    candidate = run_dir / "replay" / "replay_index.json"
    return candidate if candidate.exists() else None


def _build_entry(run_dir: Path) -> dict[str, Any]:
    run_id, ts_utc = _run_id_from_dir(run_dir)
    replay_index = _find_replay_index(run_dir)
    decision_cards = ""
    if replay_index:
        payload = _safe_read_json(replay_index)
        pointers = payload.get("pointers", {}) if isinstance(payload.get("pointers"), dict) else {}
        decision_cards = str(pointers.get("decision_cards") or "")
    return {
        "run_id": run_id,
        "ts_utc": ts_utc,
        "run_dir": to_repo_relative(run_dir),
        "replay_index": to_repo_relative(replay_index) if replay_index else "",
        "decision_cards": decision_cards,
    }


def build_recent_runs_index(runs_root: Path = RUNS_ROOT, max_runs: int | None = None) -> dict[str, Any]:
    max_runs = max_runs or _resolve_max_runs()
    run_dirs = _collect_run_dirs(runs_root)
    selected = run_dirs[:max_runs]
    entries = [_build_entry(run_dir) for run_dir in selected]
    return {
        "schema_version": 1,
        "created_ts_utc": _iso_now(),
        "runs_root": to_repo_relative(runs_root),
        "max_runs": max_runs,
        "run_count_total": len(run_dirs),
        "runs": entries,
    }


def write_recent_runs_index(
    payload: dict[str, Any],
    index_path: Path = DEFAULT_INDEX_PATH,
    latest_path: Path = DEFAULT_LATEST_PATH,
) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(index_path, payload)
    atomic_write_json(latest_path, payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build recent runs index (SIM-only, READ_ONLY)")
    parser.add_argument("--runs-root", default=str(RUNS_ROOT), help="Runs root (default Logs/train_runs)")
    parser.add_argument("--max-runs", type=int, default=None, help="Max run entries to include")
    parser.add_argument("--output", default=str(DEFAULT_INDEX_PATH), help="Path for recent_runs_index.json")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    runs_root = Path(args.runs_root)
    output_path = Path(args.output)
    latest_path = output_path.parent / "_latest" / "recent_runs_index_latest.json"
    payload = build_recent_runs_index(runs_root=runs_root, max_runs=args.max_runs)
    write_recent_runs_index(payload, output_path, latest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
