from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def logs_dir() -> Path:
    return repo_root() / "Logs"


def runtime_dir() -> Path:
    return logs_dir() / "runtime"


def policy_registry_seed_path() -> Path:
    return repo_root() / "Data" / "policy_registry.seed.json"


def policy_registry_runtime_path() -> Path:
    return runtime_dir() / "policy_registry.json"


def to_repo_relative(path: Path) -> str:
    root = repo_root()
    try:
        relative = path.resolve().relative_to(root.resolve())
        return relative.as_posix()
    except Exception:
        return str(path)
