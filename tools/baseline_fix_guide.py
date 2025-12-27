from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.git_baseline_probe import probe_baseline
LOGS_DIR = ROOT / "Logs"
DEFAULT_OUTPUT = LOGS_DIR / "baseline_guide.txt"


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _origin_remote_lines() -> list[str]:
    result = _run_git(["remote", "-v"])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _head_state() -> str:
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode != 0:
        return "HEAD"
    return result.stdout.strip() or "HEAD"


def _classify_baseline() -> tuple[str, str, str]:
    info = probe_baseline()
    status = info.get("status") or "UNAVAILABLE"
    baseline = info.get("baseline") or "unavailable"
    details = info.get("details") or "unknown"

    if status == "AVAILABLE":
        return "AVAILABLE", baseline, "baseline_available"
    if details == "no_origin":
        return "UNAVAILABLE_NO_REMOTE", baseline, "no_origin_remote"
    if details == "no_main_ref":
        return "UNAVAILABLE_NO_MAIN_REF", baseline, "missing_main_ref"
    if details in {"shallow_repo", "git_error"}:
        return "UNAVAILABLE_SHALLOW_OR_PERMS", baseline, details
    return "UNAVAILABLE_SHALLOW_OR_PERMS", baseline, "unknown"


def _format_instructions(classification: str, baseline: str, head: str, origin_lines: list[str]) -> str:
    lines: list[str] = []
    lines.append("Baseline Diagnostics Guide (SIM-only, READ_ONLY)")
    lines.append("")
    lines.append(f"HEAD state: {head}")
    lines.append(f"Baseline classification: {classification}")
    lines.append(f"Detected baseline: {baseline}")
    lines.append("")
    lines.append("Git remotes:")
    if origin_lines:
        lines.extend([f"  {line}" for line in origin_lines])
    else:
        lines.append("  (no git remotes detected)")
    lines.append("")
    lines.append("Safe manual commands (copy/paste, do NOT run automatically):")

    if classification == "UNAVAILABLE_NO_REMOTE":
        lines.extend(
            [
                "  # Add your origin remote (replace <PASTE_REMOTE_URL>):",
                "  git remote add origin <PASTE_REMOTE_URL>",
                "  git remote -v",
            ]
        )
    elif classification == "UNAVAILABLE_NO_MAIN_REF":
        lines.extend(
            [
                "  # Fetch refs from origin and list branches:",
                "  git fetch origin",
                "  git branch -a",
                "  # If origin/main exists:",
                "  git switch -c main --track origin/main",
                "  # If origin/master exists:",
                "  git switch -c master --track origin/master",
            ]
        )
    elif classification == "UNAVAILABLE_SHALLOW_OR_PERMS":
        lines.extend(
            [
                "  # If repository is shallow, unshallow it (manual approval):",
                "  git fetch --unshallow",
                "  # If permissions or git errors occur, confirm repo access and try again:",
                "  git rev-parse --is-inside-work-tree",
            ]
        )
    else:
        lines.extend(
            [
                "  # Baseline appears available. If you need a local main branch:",
                "  git switch -c main --track origin/main",
            ]
        )

    lines.append("")
    lines.append("Safety notice: DO NOT RUN git clean -fdx (risk of data loss).")
    return "\n".join(lines)


def _write_output(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Print baseline diagnostics and safe fix guidance.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Optional output path for guide text (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print report only; do not write any files.",
    )
    args = parser.parse_args()

    classification, baseline, reason = _classify_baseline()
    head = _head_state()
    origin_lines = _origin_remote_lines()
    guide = _format_instructions(classification, baseline, head, origin_lines)

    status = "OK" if classification == "AVAILABLE" else "WARN"
    print("BASELINE_GUIDE_START")
    print(
        f"BASELINE_GUIDE_SUMMARY|status={status}|baseline={baseline}|reason={reason}"
    )
    print(guide)
    print("BASELINE_GUIDE_END")

    if not args.report_only and args.output:
        _write_output(args.output, guide)
        print(f"BASELINE_GUIDE_WRITTEN|path={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
