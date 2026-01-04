from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from tools.verify_edits_contract import REQUIRED_KEYS


def _normalize_newlines(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_newlines(content)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(normalized)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True)
    text = _normalize_newlines(text)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="strict")


def _read_stdin() -> str:
    data = sys.stdin.buffer.read()
    return data.decode("utf-8-sig", errors="strict")


def _now_ny_iso() -> str:
    now = datetime.now(ZoneInfo("America/New_York")).replace(microsecond=0)
    return now.isoformat(timespec="seconds")


def _scaffold_payload(edits: list[Any]) -> dict[str, Any]:
    return {
        "version": "v1",
        "created_at": _now_ny_iso(),
        "assumptions": [],
        "risks": [],
        "gates": [],
        "rollback": [],
        "edits": edits,
    }


def _extract_payload(payload: Any) -> tuple[dict[str, Any], str]:
    if isinstance(payload, list):
        return _scaffold_payload(payload), "edits_array"
    if not isinstance(payload, dict):
        raise ValueError("json_not_object_or_array")

    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if not missing:
        return payload, "passthrough"

    edits = payload.get("edits")
    if not isinstance(edits, list):
        raise ValueError("missing_edits_list")
    return _scaffold_payload(edits), "edits_object"


def _write_summary(
    summary_path: Path,
    status: str,
    mode: str,
    input_source: str,
    output_path: Path,
    reason: str = "",
) -> None:
    lines = [
        "SCAFFOLD_EDITS_PAYLOAD_START",
        f"SCAFFOLD_EDITS_PAYLOAD_INPUT|source={input_source}",
        f"SCAFFOLD_EDITS_PAYLOAD_SUMMARY|status={status}|mode={mode}|output={output_path}",
    ]
    if reason:
        lines.append(f"SCAFFOLD_EDITS_PAYLOAD_REASON|detail={reason}")
    lines.append("SCAFFOLD_EDITS_PAYLOAD_END")
    _write_text(summary_path, "\n".join(lines))


def _fail(summary_path: Path, output_path: Path, input_source: str, reason: str) -> int:
    _write_summary(summary_path, "FAIL", "error", input_source, output_path, reason)
    _write_json(
        output_path,
        {
            "status": "FAIL",
            "reason": reason,
            "input_source": input_source,
            "output_path": str(output_path),
        },
    )
    print(f"SCAFFOLD_EDITS_PAYLOAD_SUMMARY|status=FAIL|reason={reason}|output={output_path}")
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edits-json")
    ap.add_argument("--artifacts-dir", default="artifacts")
    args = ap.parse_args(argv)

    artifacts_dir = Path(args.artifacts_dir).resolve()
    output_path = artifacts_dir / "scaffold_edits_payload.json"
    summary_path = artifacts_dir / "scaffold_edits_payload.txt"

    if args.edits_json:
        input_path = Path(args.edits_json).resolve()
        input_source = str(input_path)
        try:
            raw_text = _read_text(input_path)
        except Exception as exc:
            return _fail(summary_path, output_path, input_source, f"read_failed:{type(exc).__name__}:{exc}")
    else:
        input_source = "stdin"
        try:
            raw_text = _read_stdin()
        except Exception as exc:
            return _fail(summary_path, output_path, input_source, f"stdin_failed:{type(exc).__name__}:{exc}")

    if not raw_text.strip():
        return _fail(summary_path, output_path, input_source, "empty_input")

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return _fail(summary_path, output_path, input_source, f"json_parse_error:{exc.msg}")

    try:
        output_payload, mode = _extract_payload(payload)
    except ValueError as exc:
        return _fail(summary_path, output_path, input_source, str(exc))

    _write_json(output_path, output_payload)
    _write_summary(summary_path, "PASS", mode, input_source, output_path)
    print(f"SCAFFOLD_EDITS_PAYLOAD_SUMMARY|status=PASS|mode={mode}|output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
