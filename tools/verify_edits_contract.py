from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_KEYS = ("version", "created_at", "edits", "assumptions", "risks", "gates", "rollback")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_iso8601(value: str) -> bool:
    if not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="strict")


def _decode_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    if not text.strip():
        return None, "empty_file"
    if "```" in text:
        return None, "markdown_fence_detected"
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return None, "leading_prose_or_non_object"
    decoder = json.JSONDecoder()
    try:
        payload, idx = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        return None, f"json_parse_error:{exc.msg}"
    tail = stripped[idx:].strip()
    if tail:
        return None, "multiple_json_objects"
    if not isinstance(payload, dict):
        return None, "json_not_object"
    return payload, None


def _validate_payload(payload: dict[str, Any]) -> str | None:
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        return f"missing_keys:{','.join(missing)}"
    if payload.get("version") != "v1":
        return "invalid_version"
    created_at = payload.get("created_at")
    if not isinstance(created_at, str) or not _parse_iso8601(created_at):
        return "invalid_created_at"
    edits = payload.get("edits")
    if not isinstance(edits, list):
        return "edits_not_array"
    for key in ("assumptions", "risks", "gates", "rollback"):
        value = payload.get(key)
        if not isinstance(value, list):
            return f"{key}_not_array"
    return None


def _run_case(path: Path) -> tuple[bool, str | None]:
    try:
        text = _load_text(path)
    except Exception as exc:
        return False, f"read_failed:{type(exc).__name__}:{exc}"
    payload, reason = _decode_json(text)
    if reason:
        return False, reason
    reason = _validate_payload(payload or {})
    if reason:
        return False, reason
    return True, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts-dir", default="artifacts")
    ap.add_argument("--fixtures-dir", default="fixtures/edits_contract")
    args = ap.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    fixtures_dir = Path(args.fixtures_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    cases: list[tuple[str, Path, bool]] = [
        ("valid_sample", fixtures_dir / "good.json", True),
        ("empty_file", fixtures_dir / "bad_empty.txt", False),
        ("prose_only", fixtures_dir / "bad_prose.txt", False),
        ("fenced_output", fixtures_dir / "bad_fenced.txt", False),
        ("missing_version", fixtures_dir / "bad_missing_version.json", False),
        ("missing_edits", fixtures_dir / "bad_missing_edits.json", False),
        ("edits_not_array", fixtures_dir / "bad_edits_not_array.json", False),
        ("multiple_objects", fixtures_dir / "bad_multiple_objects.txt", False),
    ]

    lines = ["VERIFY_EDITS_CONTRACT_START"]
    failing_case = ""
    failure_reason = ""

    for name, path, should_pass in cases:
        ok, reason = _run_case(path)
        status = "PASS" if ok else "FAIL"
        lines.append(f"VERIFY_EDITS_CONTRACT_CASE|name={name}|status={status}|path={path}")
        if ok != should_pass and not failing_case:
            failing_case = name
            failure_reason = reason or "unexpected_result"

    status = "PASS" if not failing_case else "FAIL"
    summary = f"VERIFY_EDITS_CONTRACT_SUMMARY|status={status}|artifacts={artifacts_dir}"
    lines.append(summary)
    lines.append("VERIFY_EDITS_CONTRACT_END")

    _write_text(artifacts_dir / "verify_edits_contract.txt", "\n".join(lines))
    _write_json(
        artifacts_dir / "verify_edits_contract.json",
        {
            "status": status,
            "failing_case": failing_case or None,
            "reason": failure_reason or None,
            "next": "inspect artifacts/verify_edits_contract.txt",
        },
    )

    print("\n".join(lines))
    if status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
