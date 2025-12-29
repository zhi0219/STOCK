from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Tuple

from tools.paths import policy_registry_runtime_path, policy_registry_seed_path, to_repo_relative

REGISTRY_PATH = policy_registry_runtime_path()
SEED_PATH = policy_registry_seed_path()
WHITELIST_KEYS = {
    "max_orders_per_minute",
    "max_notional_per_order",
    "max_daily_loss",
    "max_drawdown",
    "min_interval_seconds",
    "max_orders_per_day",
    "cooldown_seconds",
    "min_gap_seconds",
}


def _atomic_write(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _default_registry() -> Dict[str, object]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    baseline = {
        "policy_version": "baseline",
        "risk_overrides": {},
        "created_at": now,
        "source": "seed",
    }
    return {
        "current_policy_version": "baseline",
        "policies": {"baseline": baseline},
        "history": [],
    }


def _load_seed_registry() -> Dict[str, object]:
    if not SEED_PATH.exists():
        return _default_registry()
    try:
        payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_registry()
    return payload if isinstance(payload, dict) and payload else _default_registry()


def _normalize_evidence(evidence: str) -> str:
    if not evidence:
        return evidence
    head, sep, tail = evidence.partition("#")
    path = Path(head)
    if path.is_absolute() or path.exists():
        return to_repo_relative(path) + (sep + tail if sep else "")
    return evidence


def load_registry() -> Dict[str, object]:
    if not REGISTRY_PATH.exists():
        registry = _load_seed_registry()
        _atomic_write(REGISTRY_PATH, registry)
        return registry
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        registry = _load_seed_registry()
        _atomic_write(REGISTRY_PATH, registry)
        return registry


def _normalize_policy(policy: Dict[str, object], version: str) -> Dict[str, object]:
    normalized: Dict[str, object] = {
        "policy_version": version,
        "created_at": policy.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": policy.get("source", "unknown"),
    }
    overrides = policy.get("risk_overrides") or {}
    normalized_overrides: Dict[str, object] = {}
    for key, value in overrides.items():
        if key in WHITELIST_KEYS:
            normalized_overrides[key] = value
    normalized["risk_overrides"] = normalized_overrides
    return normalized


def get_policy(version: str | None = None) -> Tuple[str, Dict[str, object]]:
    registry = load_registry()
    target = version or registry.get("current_policy_version") or "baseline"
    policies = registry.get("policies") or {}
    policy = policies.get(target)
    if not policy:
        target = "baseline"
        policy = policies.get("baseline") or _default_registry()["policies"]["baseline"]
    return target, _normalize_policy(policy, target)


def upsert_policy(policy_version: str, risk_overrides: Dict[str, object], based_on: str, source: str, evidence: str) -> Dict[str, object]:
    registry = load_registry()
    normalized_overrides = {k: v for k, v in risk_overrides.items() if k in WHITELIST_KEYS}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    policies = registry.get("policies") or {}
    policies[policy_version] = {
        "policy_version": policy_version,
        "risk_overrides": normalized_overrides,
        "created_at": now,
        "based_on": based_on,
        "source": source,
        "evidence": _normalize_evidence(evidence),
    }
    registry["policies"] = policies
    _atomic_write(REGISTRY_PATH, registry)
    return registry


def record_history(action: str, policy_version: str, evidence: str) -> None:
    registry = load_registry()
    history = registry.get("history") or []
    history.append(
        {
            "action": action,
            "policy_version": policy_version,
            "evidence": _normalize_evidence(evidence),
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    registry["history"] = history
    _atomic_write(REGISTRY_PATH, registry)


def promote_policy(policy_version: str, evidence: str) -> Dict[str, object]:
    registry = load_registry()
    registry["current_policy_version"] = policy_version
    history = registry.get("history") or []
    history.append(
        {
            "action": "PROMOTED",
            "policy_version": policy_version,
            "evidence": _normalize_evidence(evidence),
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    registry["history"] = history
    _atomic_write(REGISTRY_PATH, registry)
    return registry


def reject_policy(policy_version: str, evidence: str) -> Dict[str, object]:
    registry = load_registry()
    history = registry.get("history") or []
    history.append(
        {
            "action": "REJECTED",
            "policy_version": policy_version,
            "evidence": _normalize_evidence(evidence),
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    registry["history"] = history
    _atomic_write(REGISTRY_PATH, registry)
    return registry
