from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _die(reason: str, detail: str = "") -> None:
    msg = f"normalize_edits_failed|reason={reason}"
    if detail:
        msg = f"{msg}|detail={detail}"
    print(msg)
    raise SystemExit(2)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="strict")
    except Exception as exc:
        _die("read_failed", f"{type(exc).__name__}:{exc}")
    raise AssertionError("unreachable")


def _extract_json_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    while idx < length:
        brace_idx = text.find("{", idx)
        if brace_idx == -1:
            break
        try:
            obj, end_idx = decoder.raw_decode(text[brace_idx:])
        except json.JSONDecodeError:
            idx = brace_idx + 1
            continue
        if isinstance(obj, dict):
            candidates.append(obj)
        idx = brace_idx + max(1, end_idx)
    return candidates


def _iter_dicts(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _iter_dicts(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_dicts(item)


def _locate_ops(payload: dict[str, Any]) -> list[Any]:
    hits: list[list[Any]] = []
    for candidate in _iter_dicts(payload):
        for key in ("edits", "ops", "operations"):
            value = candidate.get(key)
            if isinstance(value, list):
                hits.append(value)
    if not hits:
        _die("missing_ops", "no_edits_or_ops_array_found")
    if len(hits) > 1:
        _die("ambiguous_ops", f"found_multiple_ops_arrays={len(hits)}")
    return hits[0]


def _ensure_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        _die("invalid_field", f"{key}_not_list")
    return value


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def normalize_payload(raw_text: str) -> dict[str, Any]:
    raw_text = raw_text.strip()
    if not raw_text:
        _die("empty_input")

    payload: dict[str, Any] | None = None
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        payload = None

    if payload is None:
        candidates = _extract_json_candidates(raw_text)
        if not candidates:
            _die("json_not_found")
        if len(candidates) > 1:
            _die("multiple_json_objects", f"count={len(candidates)}")
        payload = candidates[0]

    edits = _locate_ops(payload)
    normalized = {
        "version": "v1",
        "created_at": _now_utc_iso(),
        "edits": edits,
        "assumptions": _ensure_list(payload, "assumptions"),
        "risks": _ensure_list(payload, "risks"),
        "gates": _ensure_list(payload, "gates"),
        "rollback": _ensure_list(payload, "rollback"),
    }
    return normalized


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not input_path.exists():
        _die("missing_input", str(input_path))

    raw_text = _read_text(input_path)
    normalized = normalize_payload(raw_text)
    _write_json(output_path, normalized)
    print(f"NORMALIZE_EDITS_OK|output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
