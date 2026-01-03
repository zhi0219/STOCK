from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.verify_edits_contract import _decode_json, _load_text, _validate_payload


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edits-path", required=True)
    ap.add_argument("--artifacts-dir", default="artifacts")
    args = ap.parse_args()

    edits_path = Path(args.edits_path).resolve()
    artifacts_dir = Path(args.artifacts_dir).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    lines = ["VERIFY_EDITS_PAYLOAD_START"]
    status = "PASS"
    reason = ""

    try:
        text = _load_text(edits_path)
    except Exception as exc:
        status = "FAIL"
        reason = f"read_failed:{type(exc).__name__}:{exc}"
        text = ""

    payload: dict[str, Any] | None = None
    if status == "PASS":
        payload_any, decode_reason = _decode_json(text)
        if decode_reason:
            status = "FAIL"
            reason = decode_reason
        elif isinstance(payload_any, dict):
            payload = payload_any
        else:
            status = "FAIL"
            reason = "json_not_object"

    if status == "PASS":
        validate_reason = _validate_payload(payload or {})
        if validate_reason:
            status = "FAIL"
            reason = validate_reason

    lines.append(f"VERIFY_EDITS_PAYLOAD_FILE|path={edits_path}|status={status}")
    lines.append(
        f"VERIFY_EDITS_PAYLOAD_SUMMARY|status={status}|reason={reason or 'none'}|artifacts={artifacts_dir}"
    )
    lines.append("VERIFY_EDITS_PAYLOAD_END")

    _write_text(artifacts_dir / "verify_edits_payload.txt", "\n".join(lines))
    _write_json(
        artifacts_dir / "verify_edits_payload.json",
        {
            "status": status,
            "reason": reason or None,
            "edits_path": str(edits_path),
            "next": "inspect artifacts/verify_edits_payload.txt",
        },
    )

    print("\n".join(lines))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
