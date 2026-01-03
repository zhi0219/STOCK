from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n")


def _fail(reason: str, raw_text: str, raw_path: Path, out_path: Path) -> int:
    excerpt = raw_text[:200]
    error_payload = {
        "status": "FAIL",
        "reason": reason,
        "excerpt": excerpt,
        "raw_path": str(raw_path),
        "out_path": str(out_path),
        "next": "inspect extractor error artifacts",
    }
    error_json = out_path.with_suffix(out_path.suffix + ".error.json")
    error_txt = out_path.with_suffix(out_path.suffix + ".error.txt")
    _write_json(error_json, error_payload)
    _write_text(
        error_txt,
        "\n".join(
            [
                "EXTRACT_JSON_STRICT_ERROR",
                f"reason={reason}",
                f"raw_path={raw_path}",
                f"out_path={out_path}",
                "excerpt=",
                excerpt,
            ]
        ),
    )
    return 2


def _decode_json(raw_text: str) -> tuple[Any | None, str | None]:
    if not raw_text.strip():
        return None, "empty_file"
    stripped = raw_text.lstrip()
    if not stripped.startswith("{") and not stripped.startswith("["):
        return None, "leading_non_json"
    decoder = json.JSONDecoder()
    try:
        payload, idx = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        return None, f"json_parse_error:{exc.msg}"
    tail = stripped[idx:].strip()
    if tail:
        return None, "trailing_non_json"
    return payload, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-text", required=True)
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args()

    raw_path = Path(args.raw_text).resolve()
    out_path = Path(args.out_json).resolve()

    try:
        raw_text = raw_path.read_text(encoding="utf-8", errors="strict")
    except Exception as exc:
        return _fail(f"read_failed:{type(exc).__name__}:{exc}", "", raw_path, out_path)

    payload, reason = _decode_json(raw_text)
    if reason:
        return _fail(reason, raw_text, raw_path, out_path)

    _write_json(out_path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
