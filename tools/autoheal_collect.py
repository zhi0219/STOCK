from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize_run_id(run_id: str) -> str:
    invalid = set('<>:/\\|?*')
    return "".join("_" if ch in invalid else ch for ch in run_id)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_command(cmd: list[str], cwd: Path | None = None) -> dict[str, str | int]:
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - safety net
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _run_git(args: list[str], repo_root: Path) -> dict[str, str | int]:
    return _run_command(["git", *args], cwd=repo_root)


def _hash_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _tail_lines(path: Path, max_lines: int = 200) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


def _maybe_ps_version(exe_name: str) -> str:
    exe = shutil.which(exe_name)
    if not exe:
        return ""
    result = _run_command([exe, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
    if result["exit_code"] != 0:
        return ""
    return str(result["stdout"])


def _collect_hashes(repo_root: Path, paths: Iterable[Path]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in paths:
        absolute = repo_root / path
        entry = OrderedDict()
        entry["path"] = path.as_posix()
        if absolute.exists():
            entry["sha256"] = _hash_file(absolute)
            entry["exists"] = "true"
        else:
            entry["sha256"] = ""
            entry["exists"] = "false"
        entries.append(entry)
    return entries


def _collect_log_tails(repo_root: Path, log_paths: Iterable[Path]) -> list[dict[str, str | int]]:
    tails: list[dict[str, str | int]] = []
    for path in log_paths:
        absolute = repo_root / path
        lines = _tail_lines(absolute)
        entry: dict[str, str | int] = OrderedDict()
        entry["path"] = path.as_posix()
        entry["line_count"] = len(lines)
        entry["tail"] = "\n".join(lines)
        tails.append(entry)
    return tails


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect autoheal evidence artifacts.")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts") / "autoheal",
        help="Artifacts directory root for autoheal output.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Optional repo root override.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cwd = Path.cwd()
    repo_root = args.repo_root
    if repo_root is None:
        git_root = _run_command(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
        if git_root["exit_code"] == 0 and git_root["stdout"]:
            repo_root = Path(str(git_root["stdout"]))
        else:
            repo_root = cwd

    ts_utc = _ts_utc()
    run_id = f"{ts_utc}-{os.getpid()}"
    fs_run_id = _sanitize_run_id(run_id)

    artifacts_root = args.artifacts_dir
    run_dir = artifacts_root / fs_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    git_version = _run_git(["--version"], repo_root)
    git_head = _run_git(["rev-parse", "HEAD"], repo_root)
    git_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    git_upstream = _run_git([
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
    ], repo_root)
    git_status_before = _run_git(["status", "--porcelain"], repo_root)
    git_status_after = _run_git(["status", "--porcelain"], repo_root)

    hashes = _collect_hashes(
        repo_root,
        [
            Path("scripts/safe_pull_v1.ps1"),
            Path("scripts/win_daily_green_v1.ps1"),
            Path("scripts/ci_gates.sh"),
            Path("tools/autoheal_collect.py"),
            Path("tools/verify_autoheal_contract.py"),
            Path("tools/verify_safe_pull_contract.py"),
            Path("tools/verify_win_daily_green_contract.py"),
            Path(".github/workflows/windows_foundation.yml"),
        ],
    )

    log_tails = _collect_log_tails(
        repo_root,
        [
            Path("artifacts/gates.log"),
            Path("artifacts/verify_safe_pull_contract.txt"),
            Path("artifacts/verify_win_daily_green_contract.txt"),
            Path("artifacts/safe_pull_contract.txt"),
        ],
    )

    ps_versions = OrderedDict()
    ps_versions["powershell"] = _maybe_ps_version("powershell")
    ps_versions["pwsh"] = _maybe_ps_version("pwsh")

    evidence = OrderedDict()
    evidence["run_id"] = run_id
    evidence["fs_run_id"] = fs_run_id
    evidence["ts_utc"] = ts_utc
    evidence["repo_root"] = str(repo_root.resolve())
    evidence["cwd"] = str(cwd.resolve())
    evidence["artifacts_root"] = str(artifacts_root.resolve())
    evidence["run_dir"] = str(run_dir.resolve())
    evidence["python"] = OrderedDict(
        [
            ("executable", sys.executable),
            ("version", platform.python_version()),
        ]
    )
    evidence["platform"] = OrderedDict(
        [
            ("system", platform.system()),
            ("release", platform.release()),
            ("machine", platform.machine()),
        ]
    )
    evidence["ps_versions"] = ps_versions
    evidence["git"] = OrderedDict(
        [
            ("version", git_version["stdout"]),
            ("head_sha", git_head["stdout"]),
            ("branch", git_branch["stdout"]),
            ("upstream", git_upstream["stdout"]),
            ("status_porcelain_before", git_status_before["stdout"]),
            ("status_porcelain_after", git_status_after["stdout"]),
        ]
    )
    evidence["hashes"] = hashes
    evidence["log_tails"] = log_tails

    _write_json(run_dir / "EVIDENCE_INDEX.json", evidence)

    summary_lines = [
        f"run_id={run_id}",
        f"fs_run_id={fs_run_id}",
        f"ts_utc={ts_utc}",
        f"repo_root={evidence['repo_root']}",
        f"cwd={evidence['cwd']}",
        f"artifacts_root={evidence['artifacts_root']}",
        f"run_dir={evidence['run_dir']}",
        f"git_version={evidence['git']['version']}",
        f"git_head={evidence['git']['head_sha']}",
        f"git_branch={evidence['git']['branch']}",
        f"git_upstream={evidence['git']['upstream']}",
    ]
    _write_text(run_dir / "EVIDENCE_INDEX.txt", "\n".join(summary_lines))

    _write_text(artifacts_root / "_latest.txt", str(run_dir.resolve()))
    try:
        rel_run_dir = run_dir.resolve().relative_to(repo_root.resolve())
        _write_text(artifacts_root / "_latest_rel.txt", rel_run_dir.as_posix())
    except ValueError:
        _write_text(artifacts_root / "_latest_rel.txt", str(run_dir.resolve()))

    print("AUTOHEAL_COLLECT_START")
    print(f"AUTOHEAL_COLLECT_SUMMARY|run_id={run_id}|fs_run_id={fs_run_id}")
    print("AUTOHEAL_COLLECT_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
