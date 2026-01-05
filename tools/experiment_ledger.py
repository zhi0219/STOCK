from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tools.paths import repo_root

ROOT = repo_root()
DEFAULT_BASELINES = ["DoNothing", "Buy&Hold", "SimpleMomentum"]


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
    return entry


def append_entry(artifacts_dir: Path, entry: dict[str, object]) -> Path:
    ledger_path = artifacts_dir / "experiment_ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return ledger_path


__all__ = [
    "DEFAULT_BASELINES",
    "ROOT",
    "append_entry",
    "build_entry",
    "hash_files",
    "hash_payload",
]
