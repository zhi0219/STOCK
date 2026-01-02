import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

# P0: hard-coded allowlist (do not trust JSON for this)
ALLOW_PREFIXES = (
    "docs/",
    "tools/",
    "scripts/",
    ".github/",
    "tests/",
)

def die(msg: str, code: int = 2) -> None:
    print(msg)
    raise SystemExit(code)

def to_posix_rel(path_str: str) -> str:
    if path_str is None:
        raise ValueError("path_null")
    s = path_str.replace("\\", "/").strip()
    if not s:
        raise ValueError("path_empty")
    # Disallow absolute paths and drive letters
    if s.startswith("/") or (len(s) >= 2 and s[1] == ":"):
        raise ValueError("path_not_relative")
    parts: List[str] = []
    for p in s.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            raise ValueError("path_traversal")
        parts.append(p)
    return "/".join(parts)

def is_allowed(rel_posix: str) -> bool:
    return any(rel_posix == pref[:-1] or rel_posix.startswith(pref) for pref in ALLOW_PREFIXES)

def read_text_strict(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="strict")

def write_text_utf8_nobom_lf(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    # Enforce LF for determinism
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    with p.open("w", encoding="utf-8", newline="\n") as f:
        f.write(t)

@dataclass
class EditResult:
    idx: int
    op: str
    path: str
    status: str
    detail: str = ""

def apply_file_write(repo: Path, rel: str, edit: Dict[str, Any], dry_run: bool) -> str:
    content = edit.get("content")
    if content is None:
        raise ValueError("missing_content")
    dst = repo / Path(rel)
    if dry_run:
        return "DRY_RUN"
    write_text_utf8_nobom_lf(dst, str(content))
    return "WROTE"

def apply_anchor_edit(repo: Path, rel: str, edit: Dict[str, Any], dry_run: bool) -> str:
    anchor_start = edit.get("anchor_start")
    anchor_end = edit.get("anchor_end")
    replacement = edit.get("replacement")
    if anchor_start is None or anchor_end is None or replacement is None:
        raise ValueError("missing_anchor_fields")
    target = repo / Path(rel)
    if not target.exists():
        raise ValueError("target_missing")
    text = read_text_strict(target)

    cs = text.count(anchor_start)
    if cs != 1:
        raise ValueError(f"anchor_start_hits={cs}")
    start_idx = text.index(anchor_start)

    ce = text.count(anchor_end)
    if ce != 1:
        raise ValueError(f"anchor_end_hits={ce}")
    end_idx = text.index(anchor_end, start_idx + len(anchor_start))
    end_idx2 = end_idx + len(anchor_end)

    new_text = text[:start_idx] + str(replacement) + text[end_idx2:]
    if dry_run:
        return "DRY_RUN"
    write_text_utf8_nobom_lf(target, new_text)
    return "EDITED"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--edits", required=True)
    ap.add_argument("--artifacts-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    artifacts = Path(args.artifacts_dir).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)

    edits_path = Path(args.edits).resolve()
    if not edits_path.exists():
        die(f"APPLY_EDITS_SUMMARY|status=FAIL|reason=missing_edits_file|edits={edits_path}")

    try:
        payload = json.loads(edits_path.read_text(encoding="utf-8", errors="strict"))
    except Exception as e:
        die(f"APPLY_EDITS_SUMMARY|status=FAIL|reason=edits_json_parse_error|detail={type(e).__name__}:{e}")

    version = str(payload.get("version", ""))
    if not version:
        die("APPLY_EDITS_SUMMARY|status=FAIL|reason=missing_version")

    edits = payload.get("edits")
    if not isinstance(edits, list) or len(edits) == 0:
        die("APPLY_EDITS_SUMMARY|status=FAIL|reason=missing_edits_list")

    results: List[EditResult] = []
    failed = 0

    print("APPLY_EDITS_START")
    for i, ed in enumerate(edits):
        try:
            op = str(ed.get("op", "")).strip()
            rel = to_posix_rel(str(ed.get("path", "")))
            if not is_allowed(rel):
                raise ValueError("path_not_in_allowlist")
            if op == "FILE_WRITE":
                detail = apply_file_write(repo, rel, ed, args.dry_run)
                results.append(EditResult(i, op, rel, "PASS", detail))
                print(f"APPLY_EDITS_ITEM|idx={i}|op={op}|path={rel}|status=PASS|detail={detail}")
            elif op == "ANCHOR_EDIT":
                detail = apply_anchor_edit(repo, rel, ed, args.dry_run)
                results.append(EditResult(i, op, rel, "PASS", detail))
                print(f"APPLY_EDITS_ITEM|idx={i}|op={op}|path={rel}|status=PASS|detail={detail}")
            else:
                raise ValueError("unknown_op")
        except Exception as e:
            failed += 1
            results.append(EditResult(i, str(ed.get("op", "")), str(ed.get("path", "")), "FAIL", f"{type(e).__name__}:{e}"))
            print(f"APPLY_EDITS_ITEM|idx={i}|op={ed.get('op')}|path={ed.get('path')}|status=FAIL|detail={type(e).__name__}:{e}")

    out = {
        "version": version,
        "status": "PASS" if failed == 0 else "FAIL",
        "dry_run": bool(args.dry_run),
        "allow_prefixes": list(ALLOW_PREFIXES),
        "edits_file": str(edits_path),
        "results": [r.__dict__ for r in results],
    }
    (artifacts / "apply_edits_result.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    status = "PASS" if failed == 0 else "FAIL"
    print(f"APPLY_EDITS_SUMMARY|status={status}|applied={len(edits)-failed}|failed={failed}|result_json={artifacts / 'apply_edits_result.json'}")
    print("APPLY_EDITS_END")
    return 0 if failed == 0 else 3

if __name__ == "__main__":
    raise SystemExit(main())