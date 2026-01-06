from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_BASELINES = ["DoNothing", "Buy&Hold", "SimpleMomentum"]
LEDGER_POINTER_NAME = "experiment_ledger_latest.json"


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return _hash_bytes(encoded)


def hash_files(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_entry(
    run_id: str,
    candidate_count: int,
    trial_count: int,
    baselines_used: Iterable[str] | None,
    window_config: object,
    code_paths: Iterable[Path],
    timestamp: str | None = None,
    trial_budget_override: bool | None = None,
    requested_candidate_count: int | None = None,
    requested_trial_count: int | None = None,
    enforced_candidate_count: int | None = None,
    enforced_trial_count: int | None = None,
) -> dict[str, object]:
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry: dict[str, object] = {
        "run_id": run_id,
        "timestamp": ts,
        "candidate_count": int(candidate_count),
        "trial_count": int(trial_count),
        "baselines_used": list(baselines_used or []),
        "window_config_hash": hash_payload(window_config),
        "code_hash": hash_files(code_paths),
    }
    if trial_budget_override is not None:
        entry["trial_budget_override"] = bool(trial_budget_override)
    if requested_candidate_count is not None:
        entry["requested_candidate_count"] = int(requested_candidate_count)
    if requested_trial_count is not None:
        entry["requested_trial_count"] = int(requested_trial_count)
    if enforced_candidate_count is not None:
        entry["enforced_candidate_count"] = int(enforced_candidate_count)
    if enforced_trial_count is not None:
        entry["enforced_trial_count"] = int(enforced_trial_count)
    return entry


def _write_pointer(artifacts_dir: Path, run_id: str, ledger_path: Path) -> Path:
    pointer_path = artifacts_dir / LEDGER_POINTER_NAME
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ledger_path": to_repo_relative(ledger_path),
    }
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pointer_path


def append_entry(artifacts_dir: Path, entry: dict[str, object], ledger_path: Path | None = None) -> Path:
    run_id = str(entry.get("run_id") or "unknown_run")
    if ledger_path is None:
        ledger_path = artifacts_dir / f"experiment_ledger_{run_id}.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _write_pointer(artifacts_dir, run_id, ledger_path)
    return ledger_path


def resolve_latest_ledger_path(artifacts_dir: Path, fallback: Path | None = None) -> Path:
    pointer_path = artifacts_dir / LEDGER_POINTER_NAME
    if pointer_path.exists():
        try:
            payload = json.loads(pointer_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            ledger_path = payload.get("ledger_path")
            if isinstance(ledger_path, str) and ledger_path:
                return (repo_root() / ledger_path).resolve()
    return fallback if fallback is not None else artifacts_dir / "experiment_ledger.jsonl"


__all__ = [
    "DEFAULT_BASELINES",
    "LEDGER_POINTER_NAME",
    "ROOT",
    "append_entry",
    "build_entry",
    "hash_files",
    "hash_payload",
    "resolve_latest_ledger_path",
]
