from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_INDEX_KEYS = [
    "run_id",
    "fs_run_id",
    "ts_utc",
    "repo_root",
    "cwd",
    "artifacts_root",
    "run_dir",
    "ps_versions",
    "git",
    "hashes",
    "log_tails",
]

REQUIRED_GIT_KEYS = [
    "version",
    "head_sha",
    "branch",
    "upstream",
    "status_porcelain_before",
    "status_porcelain_after",
]


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify autoheal evidence contract.")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts") / "autoheal",
        help="Artifacts directory root for autoheal output.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Optional run directory override.",
    )
    return parser.parse_args(argv)


def _has_bom(path: Path) -> bool:
    if not path.exists():
        return False
    data = path.read_bytes()
    return data.startswith(b"\xef\xbb\xbf")


def _resolve_input_dir(artifacts_dir: Path, input_dir: Path | None) -> Path:
    if input_dir is not None:
        if input_dir.is_file():
            pointer_text = input_dir.read_text(encoding="utf-8", errors="replace").strip()
            if pointer_text:
                candidate = Path(pointer_text)
                if not candidate.is_absolute():
                    candidate = (Path.cwd() / candidate).resolve()
                return candidate
            return input_dir
        return input_dir
    latest_path = artifacts_dir / "_latest.txt"
    if latest_path.exists():
        pointer_text = latest_path.read_text(encoding="utf-8", errors="replace").strip()
        if pointer_text:
            candidate = Path(pointer_text)
            if not candidate.is_absolute():
                candidate = (Path.cwd() / candidate).resolve()
            return candidate
    return artifacts_dir


def _check_contract(artifacts_dir: Path, input_dir: Path) -> tuple[str, list[str]]:
    errors: list[str] = []
    latest_path = artifacts_dir / "_latest.txt"
    if not latest_path.exists():
        errors.append("missing_latest_pointer")
    else:
        latest_target = latest_path.read_text(encoding="utf-8", errors="replace").strip()
        if latest_target:
            latest_path_value = Path(latest_target)
            if not latest_path_value.is_absolute():
                latest_path_value = (Path.cwd() / latest_path_value).resolve()
            if not latest_path_value.exists():
                errors.append("latest_pointer_target_missing")

    index_json = input_dir / "EVIDENCE_INDEX.json"
    index_txt = input_dir / "EVIDENCE_INDEX.txt"
    if not index_json.exists():
        errors.append("missing_index_json")
    if not index_txt.exists():
        errors.append("missing_index_txt")

    if index_json.exists():
        if _has_bom(index_json):
            errors.append("index_json_has_bom")
        try:
            payload = json.loads(index_json.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = {}
            errors.append("index_json_invalid")
        for key in REQUIRED_INDEX_KEYS:
            if key not in payload:
                errors.append(f"index_missing_key:{key}")
        git_payload = payload.get("git") if isinstance(payload, dict) else None
        if isinstance(git_payload, dict):
            for key in REQUIRED_GIT_KEYS:
                if key not in git_payload:
                    errors.append(f"index_git_missing_key:{key}")
        else:
            errors.append("index_git_missing")

    if index_txt.exists() and _has_bom(index_txt):
        errors.append("index_txt_has_bom")

    status = "PASS" if not errors else "FAIL"
    return status, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    input_dir = _resolve_input_dir(args.artifacts_dir, args.input_dir)
    status, errors = _check_contract(args.artifacts_dir, input_dir)

    payload = {
        "status": status,
        "errors": errors,
        "artifacts_dir": args.artifacts_dir.as_posix(),
        "input_dir": input_dir.as_posix(),
        "ts_utc": _ts_utc(),
    }

    _write_json(args.artifacts_dir / "verify_autoheal_contract.json", payload)
    (args.artifacts_dir / "verify_autoheal_contract.txt").write_text(
        "\n".join(errors) if errors else "ok",
        encoding="utf-8",
    )

    print("AUTOHEAL_CONTRACT_START")
    print(f"AUTOHEAL_CONTRACT_SUMMARY|status={status}|errors={len(errors)}")
    print("AUTOHEAL_CONTRACT_END")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
