from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.paths import policy_registry_runtime_path, repo_root, to_repo_relative

ROOT = repo_root()
LEGACY_PATH = ROOT / "Logs" / "policy_registry.json"
RUNTIME_PATH = policy_registry_runtime_path()
ARTIFACT_PATH = ROOT / "artifacts" / "migrate_policy_registry_result.json"
BACKUP_DIR = ROOT / "_local_backup"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_evidence_text(text: str) -> str:
    if not text:
        return text
    head, sep, tail = text.partition("#")
    path = Path(head)
    if path.is_absolute() or path.exists():
        return to_repo_relative(path) + (sep + tail if sep else "")
    return text


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def walk(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(item, key) for item in value]
        if isinstance(value, str) and key and "evidence" in key.lower():
            return _normalize_evidence_text(value)
        return value

    return walk(payload) if isinstance(payload, dict) else payload


def _merge_registry(runtime_payload: dict[str, Any], legacy_payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(runtime_payload)
    runtime_policies = dict(runtime_payload.get("policies", {})) if isinstance(runtime_payload.get("policies"), dict) else {}
    legacy_policies = dict(legacy_payload.get("policies", {})) if isinstance(legacy_payload.get("policies"), dict) else {}
    runtime_policies = {**legacy_policies, **runtime_policies}
    merged["policies"] = runtime_policies

    runtime_history = list(runtime_payload.get("history", [])) if isinstance(runtime_payload.get("history"), list) else []
    legacy_history = list(legacy_payload.get("history", [])) if isinstance(legacy_payload.get("history"), list) else []
    merged["history"] = legacy_history + runtime_history

    for key, value in legacy_payload.items():
        if key in {"policies", "history"}:
            continue
        merged.setdefault(key, value)
    return merged


def migrate_policy_registry() -> dict[str, Any]:
    backup_path = None
    legacy_payload = _load_json(LEGACY_PATH)
    runtime_payload = _load_json(RUNTIME_PATH)
    legacy_present = LEGACY_PATH.exists()
    runtime_present = RUNTIME_PATH.exists()

    if legacy_present:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BACKUP_DIR / f"policy_registry_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        backup_path.write_text(LEGACY_PATH.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

    if legacy_present:
        if runtime_payload is None:
            merged_payload = legacy_payload or {}
        else:
            merged_payload = _merge_registry(runtime_payload, legacy_payload or {})
        merged_payload = _sanitize_payload(merged_payload)
        RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_PATH.write_text(json.dumps(merged_payload, indent=2, sort_keys=True), encoding="utf-8")
        LEGACY_PATH.unlink(missing_ok=True)
    elif runtime_payload is not None:
        sanitized = _sanitize_payload(runtime_payload)
        if sanitized != runtime_payload:
            RUNTIME_PATH.write_text(json.dumps(sanitized, indent=2, sort_keys=True), encoding="utf-8")

    result = {
        "schema_version": 1,
        "ts_utc": _iso_now(),
        "status": "MIGRATED" if legacy_present else "NOOP",
        "legacy_present": legacy_present,
        "runtime_present": runtime_present,
        "legacy_path": to_repo_relative(LEGACY_PATH),
        "runtime_path": to_repo_relative(RUNTIME_PATH),
        "backup_path": to_repo_relative(backup_path) if backup_path else None,
    }
    _write_json(ARTIFACT_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy policy registry to runtime directory.")
    parser.parse_args()
    migrate_policy_registry()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
