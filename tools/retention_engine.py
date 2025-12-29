from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.fs_atomic import atomic_write_json
from tools.paths import repo_root, runtime_dir, to_repo_relative

ROOT = repo_root()
LOGS_DIR = ROOT / "Logs"
RUNS_DIR = LOGS_DIR / "train_runs"
RUNTIME_DIR = runtime_dir()
ARTIFACTS_DIR = ROOT / "artifacts"
POLICY_PATH = ROOT / "Data" / "retention_policy.json"

RETENTION_REPORT_RUNTIME = RUNTIME_DIR / "retention_report.json"
RETENTION_REPORT_ARTIFACTS = ARTIFACTS_DIR / "retention_report.json"
PRUNE_PLAN_RUNTIME = RUNTIME_DIR / "retention_prune_plan.json"
PRUNE_RESULT_RUNTIME = RUNTIME_DIR / "retention_prune_result.json"
PRUNE_PLAN_ARTIFACTS = ARTIFACTS_DIR / "retention_prune_plan.json"
PRUNE_RESULT_ARTIFACTS = ARTIFACTS_DIR / "retention_prune_result.json"

DEFAULT_POLICY = {
    "schema_version": 1,
    "keep_days_train_runs": 14,
    "keep_runs_max": 500,
    "keep_days_replay": 14,
    "keep_replay_max_bytes_per_run": 2 * 1024 * 1024,
    "keep_diagnostics_days": 14,
    "keep_events_days": 30,
    "prune_mode": "SAFE",
    "recent_runs_buffer": 20,
    "recent_index_max_runs": 100,
}

SAFE_ROOTS = [
    LOGS_DIR,
    ARTIFACTS_DIR,
]


@dataclass(frozen=True)
class RunInfo:
    run_dir: Path
    run_id: str
    mtime: float
    age_days: float
    size_bytes: int
    replay_bytes: int


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


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for file in path.rglob("*"):
        if file.is_file():
            try:
                total += file.stat().st_size
            except OSError:
                continue
    return total


def _age_days_from_mtime(mtime: float) -> float:
    if not mtime:
        return 0.0
    return max(0.0, (_now().timestamp() - mtime) / 86400.0)


def _event_path(now: datetime) -> Path:
    return LOGS_DIR / f"events_{now:%Y-%m-%d}.jsonl"


def _write_event(event_type: str, message: str, severity: str = "INFO", **extra: Any) -> dict[str, Any]:
    now = _now()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "ts_utc": now.isoformat(),
        "event_type": event_type,
        "severity": severity,
        "message": message,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    path = _event_path(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    payload["events_path"] = to_repo_relative(path)
    return payload


def _load_policy() -> dict[str, Any]:
    payload = _safe_read_json(POLICY_PATH)
    merged = dict(DEFAULT_POLICY)
    if payload:
        merged.update({key: value for key, value in payload.items() if value is not None})
    return merged


def _env_int(key: str) -> int | None:
    value = os.environ.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _env_str(key: str) -> str | None:
    value = os.environ.get(key)
    if value in (None, ""):
        return None
    return str(value)


def resolve_policy() -> dict[str, Any]:
    policy = _load_policy()
    overrides = {
        "keep_days_train_runs": _env_int("RETENTION_KEEP_DAYS_TRAIN_RUNS"),
        "keep_runs_max": _env_int("RETENTION_KEEP_RUNS_MAX"),
        "keep_days_replay": _env_int("RETENTION_KEEP_DAYS_REPLAY"),
        "keep_replay_max_bytes_per_run": _env_int("RETENTION_KEEP_REPLAY_MAX_BYTES"),
        "keep_diagnostics_days": _env_int("RETENTION_KEEP_DIAGNOSTICS_DAYS"),
        "keep_events_days": _env_int("RETENTION_KEEP_EVENTS_DAYS"),
        "prune_mode": _env_str("RETENTION_PRUNE_MODE"),
        "recent_runs_buffer": _env_int("RETENTION_RECENT_RUNS_BUFFER"),
        "recent_index_max_runs": _env_int("RETENTION_RECENT_INDEX_MAX_RUNS"),
    }
    for key, value in overrides.items():
        if value is None:
            continue
        policy[key] = value
    return policy


def _load_tracked_paths() -> set[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    entries = result.stdout.split("\x00")
    return {entry.strip() for entry in entries if entry.strip()}


def _is_tracked(path: Path, tracked_paths: set[str]) -> bool:
    rel = to_repo_relative(path)
    return rel in tracked_paths


def _latest_pointer_paths() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    pointers: list[Path] = []
    for path in RUNS_DIR.rglob("*_latest.json"):
        pointers.append(path)
    for path in RUNS_DIR.rglob("*_latest.jsonl"):
        pointers.append(path)
    return pointers


def _latest_run_ids(latest_dir: Path) -> set[str]:
    if not latest_dir.exists():
        return set()
    run_ids: set[str] = set()
    for path in latest_dir.glob("*.json"):
        payload = _safe_read_json(path)
        run_id = payload.get("run_id")
        if run_id:
            run_ids.add(str(run_id))
    return run_ids


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
    run_dirs.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)
    return run_dirs


def _run_id_from_dir(run_dir: Path) -> str:
    run_complete = _safe_read_json(run_dir / "run_complete.json")
    run_id = run_complete.get("run_id")
    return str(run_id) if run_id else run_dir.name


def _replay_bytes(run_dir: Path) -> int:
    replay_dir = run_dir / "replay"
    if not replay_dir.exists():
        return 0
    total = 0
    for file in replay_dir.glob("*.jsonl"):
        try:
            total += file.stat().st_size
        except OSError:
            continue
    return total


def _collect_run_info(runs_root: Path) -> list[RunInfo]:
    run_dirs = _collect_run_dirs(runs_root)
    info: list[RunInfo] = []
    for run_dir in run_dirs:
        mtime = 0.0
        run_complete = run_dir / "run_complete.json"
        if run_complete.exists():
            try:
                mtime = run_complete.stat().st_mtime
            except OSError:
                mtime = 0.0
        if not mtime:
            try:
                mtime = run_dir.stat().st_mtime
            except OSError:
                mtime = 0.0
        size_bytes = _dir_size(run_dir)
        info.append(
            RunInfo(
                run_dir=run_dir,
                run_id=_run_id_from_dir(run_dir),
                mtime=mtime,
                age_days=_age_days_from_mtime(mtime),
                size_bytes=size_bytes,
                replay_bytes=_replay_bytes(run_dir),
            )
        )
    return info


def _collect_diagnostics_candidates(policy: dict[str, Any]) -> list[dict[str, Any]]:
    keep_days = float(policy.get("keep_diagnostics_days", 0))
    candidates: list[dict[str, Any]] = []
    if not RUNTIME_DIR.exists():
        return candidates
    for path in RUNTIME_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
            size = path.stat().st_size
        except OSError:
            continue
        age_days = _age_days_from_mtime(mtime)
        if age_days <= keep_days:
            continue
        candidates.append(
            {
                "path_rel": to_repo_relative(path),
                "reason": "diagnostics_age_exceeded",
                "age_days": round(age_days, 2),
                "size_bytes": size,
                "category": "diagnostics",
            }
        )
    return candidates


def _collect_events_candidates(policy: dict[str, Any]) -> list[dict[str, Any]]:
    keep_days = float(policy.get("keep_events_days", 0))
    candidates: list[dict[str, Any]] = []
    if not LOGS_DIR.exists():
        return candidates
    for path in LOGS_DIR.glob("events_*.jsonl"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
            size = path.stat().st_size
        except OSError:
            continue
        age_days = _age_days_from_mtime(mtime)
        if age_days <= keep_days:
            continue
        candidates.append(
            {
                "path_rel": to_repo_relative(path),
                "reason": "events_age_exceeded",
                "age_days": round(age_days, 2),
                "size_bytes": size,
                "category": "events",
            }
        )
    return candidates


def _collect_artifacts_candidates(policy: dict[str, Any]) -> list[dict[str, Any]]:
    keep_days = float(policy.get("keep_diagnostics_days", 0))
    artifacts_logs = ARTIFACTS_DIR / "Logs"
    candidates: list[dict[str, Any]] = []
    if not artifacts_logs.exists():
        return candidates
    for path in artifacts_logs.rglob("*"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
            size = path.stat().st_size
        except OSError:
            continue
        age_days = _age_days_from_mtime(mtime)
        if age_days <= keep_days:
            continue
        candidates.append(
            {
                "path_rel": to_repo_relative(path),
                "reason": "artifact_copy_age_exceeded",
                "age_days": round(age_days, 2),
                "size_bytes": size,
                "category": "artifacts",
            }
        )
    return candidates


def build_report() -> dict[str, Any]:
    policy = resolve_policy()
    keep_runs_max = int(policy.get("keep_runs_max", 0))
    keep_days_train = float(policy.get("keep_days_train_runs", 0))
    keep_days_replay = float(policy.get("keep_days_replay", 0))
    replay_max_bytes = int(policy.get("keep_replay_max_bytes_per_run", 0))
    recent_buffer = int(policy.get("recent_runs_buffer", 0))

    run_info = _collect_run_info(RUNS_DIR)
    sorted_runs = sorted(run_info, key=lambda item: item.mtime, reverse=True)
    keep_run_ids: set[str] = set(item.run_id for item in sorted_runs[: max(recent_buffer, 0)])

    latest_dir = RUNS_DIR / "_latest"
    latest_run_ids = _latest_run_ids(latest_dir)
    keep_run_ids.update(latest_run_ids)

    candidates: list[dict[str, Any]] = []
    for idx, info in enumerate(sorted_runs, start=1):
        beyond_max = keep_runs_max > 0 and idx > keep_runs_max
        if info.run_id in keep_run_ids:
            continue
        if beyond_max and info.age_days > keep_days_train:
            candidates.append(
                {
                    "path_rel": to_repo_relative(info.run_dir),
                    "reason": "train_runs_age_and_limit_exceeded",
                    "age_days": round(info.age_days, 2),
                    "size_bytes": info.size_bytes,
                    "category": "train_runs",
                }
            )
        if info.replay_bytes > replay_max_bytes and beyond_max and info.age_days > keep_days_replay:
            candidates.append(
                {
                    "path_rel": to_repo_relative(info.run_dir),
                    "reason": "replay_bytes_exceeded",
                    "age_days": round(info.age_days, 2),
                    "size_bytes": info.replay_bytes,
                    "category": "replay",
                }
            )

    candidates.extend(_collect_diagnostics_candidates(policy))
    candidates.extend(_collect_artifacts_candidates(policy))
    candidates.extend(_collect_events_candidates(policy))

    tracked_paths = _load_tracked_paths()
    candidates = [
        entry for entry in candidates if not _is_tracked(ROOT / entry["path_rel"], tracked_paths)
    ]

    latest_pointer_paths = _latest_pointer_paths()
    latest_pointers_protected = True
    for entry in candidates:
        path_rel = entry.get("path_rel", "")
        if "/_latest/" in path_rel or path_rel.endswith("_latest.json") or path_rel.endswith("_latest.jsonl"):
            latest_pointers_protected = False
            break

    required_files_present = True
    for pointer in latest_pointer_paths:
        if pointer.suffix != ".json":
            continue
        if not _safe_read_json(pointer):
            required_files_present = False
            break

    storage_summary = {
        "logs_bytes": _dir_size(LOGS_DIR),
        "train_runs_bytes": _dir_size(RUNS_DIR),
        "replay_bytes": sum(info.replay_bytes for info in run_info),
        "diagnostics_bytes": _dir_size(RUNTIME_DIR),
    }
    try:
        storage_summary["free_space_bytes"] = shutil.disk_usage(ROOT).free
    except Exception:
        storage_summary["free_space_bytes"] = None

    return {
        "ts_utc": _iso_now(),
        "policy": policy,
        "storage_summary": storage_summary,
        "candidates": sorted(candidates, key=lambda item: (item.get("category", ""), item.get("age_days", 0))),
        "safety_checks": {
            "latest_pointers_protected": latest_pointers_protected,
            "required_files_present": required_files_present,
        },
    }


def write_report(report: dict[str, Any], runtime_path: Path = RETENTION_REPORT_RUNTIME) -> None:
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(runtime_path, report)
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        RETENTION_REPORT_ARTIFACTS.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(RETENTION_REPORT_ARTIFACTS, report)


def _is_under_safe_root(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        resolved = path.absolute()
    for root in SAFE_ROOTS:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        else:
            return True
    return False


def _delete_path(path: Path) -> tuple[bool, str]:
    if not _is_under_safe_root(path):
        return False, "refused_outside_safe_roots"
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
        return True, "deleted_dir"
    if path.exists():
        path.unlink()
        return True, "deleted_file"
    return True, "missing"


def _filter_prune_candidates(report: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    candidates = report.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    mode = mode.upper()
    allowed_categories = {"TRAIN_RUNS", "REPLAY", "DIAGNOSTICS", "ARTIFACTS"} if mode == "SAFE" else None
    filtered: list[dict[str, Any]] = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("category", "")).upper()
        if allowed_categories is not None and category not in allowed_categories:
            continue
        filtered.append(entry)
    return filtered


def prune(mode: str, dry_run: bool = False) -> dict[str, Any]:
    report = build_report()
    safety = report.get("safety_checks", {}) if isinstance(report.get("safety_checks"), dict) else {}
    latest_ok = bool(safety.get("latest_pointers_protected", False))
    required_ok = bool(safety.get("required_files_present", False))
    policy = report.get("policy", {}) if isinstance(report.get("policy"), dict) else {}

    plan = {
        "ts_utc": _iso_now(),
        "mode": mode,
        "dry_run": dry_run,
        "policy": policy,
        "safety_checks": safety,
        "candidates": _filter_prune_candidates(report, mode),
        "status": "READY",
        "refused_reason": None,
    }

    if not latest_ok or not required_ok:
        plan["status"] = "REFUSED"
        plan["refused_reason"] = "safety_checks_failed"

    PRUNE_PLAN_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(PRUNE_PLAN_RUNTIME, plan)
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        PRUNE_PLAN_ARTIFACTS.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(PRUNE_PLAN_ARTIFACTS, plan)

    _write_event(
        "RETENTION_PRUNE_PLAN",
        "Retention prune plan recorded.",
        mode=mode,
        dry_run=dry_run,
        candidates=len(plan.get("candidates", [])),
        status=plan.get("status"),
        plan_path=to_repo_relative(PRUNE_PLAN_RUNTIME),
    )

    deleted: list[str] = []
    failed: list[dict[str, Any]] = []

    if plan["status"] == "READY" and not dry_run:
        for entry in plan["candidates"]:
            path_rel = entry.get("path_rel")
            if not path_rel:
                continue
            path = ROOT / path_rel
            try:
                ok, note = _delete_path(path)
                if ok:
                    deleted.append(path_rel)
                else:
                    failed.append({"path_rel": path_rel, "error": note})
            except Exception as exc:
                failed.append({"path_rel": path_rel, "error": str(exc)})

    status = "SUCCESS"
    if plan["status"] != "READY":
        status = "REFUSED"
    elif failed:
        status = "FAILED"

    result = {
        "ts_utc": _iso_now(),
        "mode": mode,
        "dry_run": dry_run,
        "status": status,
        "plan_path": to_repo_relative(PRUNE_PLAN_RUNTIME),
        "deleted": deleted,
        "failed": failed,
        "summary": {
            "deleted_count": len(deleted),
            "failed_count": len(failed),
            "candidate_count": len(plan.get("candidates", [])),
        },
    }

    PRUNE_RESULT_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(PRUNE_RESULT_RUNTIME, result)
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        PRUNE_RESULT_ARTIFACTS.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(PRUNE_RESULT_ARTIFACTS, result)

    _write_event(
        "RETENTION_PRUNE_COMPLETE",
        "Retention prune completed.",
        mode=mode,
        dry_run=dry_run,
        status=status,
        result_path=to_repo_relative(PRUNE_RESULT_RUNTIME),
    )

    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retention engine (SIM-only, READ_ONLY)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report", help="Generate retention report")
    report_parser.add_argument("--output", default=str(RETENTION_REPORT_RUNTIME))

    prune_parser = subparsers.add_parser("prune", help="Prune runtime artifacts")
    prune_parser.add_argument("--mode", choices=["safe"], default="safe")
    prune_parser.add_argument("--dry-run", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "report":
        report = build_report()
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_path, report)
        if output_path.resolve() != RETENTION_REPORT_RUNTIME.resolve():
            RETENTION_REPORT_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(RETENTION_REPORT_RUNTIME, report)
        if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
            RETENTION_REPORT_ARTIFACTS.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(RETENTION_REPORT_ARTIFACTS, report)
        return 0
    if args.command == "prune":
        prune(mode=args.mode, dry_run=args.dry_run)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
