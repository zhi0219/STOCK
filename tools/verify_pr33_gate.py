from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from tools.pr28_training_loop import PR28Config, RUNS_ROOT, run_pr28_flow
from tools.replay_artifacts import MAX_DECISION_CARDS_BYTES, REPLAY_SCHEMA_VERSION

ARTIFACTS_DIR = Path("artifacts")
REPLAY_ARTIFACTS_DIR = ARTIFACTS_DIR / "Logs" / "train_runs" / "_pr33_gate" / "_latest"
ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\")


class GateError(RuntimeError):
    pass


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
            else:
                return None
    except Exception:
        return None
    return rows


def _contains_absolute_path(text: str) -> bool:
    if not text:
        return False
    if text.startswith("/"):
        return True
    if ABS_PATH_PATTERN.search(text):
        return True
    if re.match(r"^[A-Za-z]:", text):
        return True
    if "\\Users\\" in text:
        return True
    if "/home/runner/" in text:
        return True
    return False


def _assert_repo_relative(paths: list[str]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        if _contains_absolute_path(path):
            errors.append(f"absolute_path_detected:{path}")
    return errors


def _copy_latest_to_artifacts(run_dir: Path) -> tuple[Path, Path]:
    replay_latest_dir = run_dir / "_latest"
    index_path = replay_latest_dir / "replay_index_latest.json"
    cards_path = replay_latest_dir / "decision_cards_latest.jsonl"
    if not index_path.exists() or not cards_path.exists():
        raise GateError("replay_latest_missing")
    REPLAY_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    target_index = REPLAY_ARTIFACTS_DIR / "replay_index_latest.json"
    target_cards = REPLAY_ARTIFACTS_DIR / "decision_cards_latest.jsonl"
    shutil.copy2(index_path, target_index)
    shutil.copy2(cards_path, target_cards)
    return target_index, target_cards


def _validate_replay_index(path: Path) -> list[str]:
    errors: list[str] = []
    payload = _safe_read_json(path)
    required = [
        "schema_version",
        "created_ts_utc",
        "run_id",
        "git_commit",
        "runner",
        "counts",
        "truncation",
        "pointers",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        errors.append(f"replay_index_missing_fields:{','.join(missing)}")
    if payload.get("schema_version") != REPLAY_SCHEMA_VERSION:
        errors.append("replay_index_schema_mismatch")
    pointers = payload.get("pointers", {}) if isinstance(payload.get("pointers"), dict) else {}
    errors.extend(_assert_repo_relative([str(value) for value in pointers.values()]))
    return errors


def _validate_decision_cards(path: Path, replay_index: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    rows = _safe_read_jsonl(path)
    if rows is None:
        return ["decision_cards_invalid_jsonl"]
    evidence_paths: list[str] = []
    for row in rows:
        evidence = row.get("evidence", {}) if isinstance(row.get("evidence"), dict) else {}
        paths = evidence.get("paths", []) if isinstance(evidence.get("paths"), list) else []
        evidence_paths.extend([str(p) for p in paths])
    errors.extend(_assert_repo_relative(evidence_paths))

    truncation = replay_index.get("truncation", {}) if isinstance(replay_index.get("truncation"), dict) else {}
    truncated = bool(truncation.get("truncated"))
    size_bytes = path.stat().st_size if path.exists() else 0
    if size_bytes > MAX_DECISION_CARDS_BYTES and not truncated:
        errors.append("decision_cards_size_exceeded_without_truncation")
    return errors


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    runs_root = RUNS_ROOT / "_pr33_gate"
    config = PR28Config(
        runs_root=runs_root,
        seed=33,
        max_steps=80,
        candidate_count=2,
        min_steps=40,
        quotes_limit=120,
    )
    artifacts = run_pr28_flow(config)
    run_dir = Path(str(artifacts.get("run_dir")))
    try:
        index_artifact, cards_artifact = _copy_latest_to_artifacts(run_dir)
    except GateError as exc:
        errors.append(str(exc))
        index_artifact = REPLAY_ARTIFACTS_DIR / "replay_index_latest.json"
        cards_artifact = REPLAY_ARTIFACTS_DIR / "decision_cards_latest.jsonl"

    index_payload = _safe_read_json(index_artifact)
    if not index_payload:
        errors.append("replay_index_missing_or_invalid")
    else:
        errors.extend(_validate_replay_index(index_artifact))

    if not cards_artifact.exists():
        errors.append("decision_cards_missing")
    else:
        errors.extend(_validate_decision_cards(cards_artifact, index_payload))

    if os.environ.get("PR33_FORCE_FAIL") == "1":
        errors.append("PR33_FORCE_FAIL")

    if errors:
        print("verify_pr33_gate FAIL")
        for reason in errors:
            print(f" - {reason}")
        return 1

    print("verify_pr33_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
