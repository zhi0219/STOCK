import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_step(name: str, path: Path) -> dict:
    if not path.exists():
        return {"name": name, "status": "SKIP", "exit": 0, "message": "not present"}
    result = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout = result.stdout or ""
    status = "PASS" if result.returncode == 0 else "FAIL"
    if "status=DEGRADED" in stdout:
        status = "DEGRADED"
    return {
        "name": name,
        "status": status,
        "exit": result.returncode,
        "stdout": stdout,
        "stderr": result.stderr or "",
    }


def main() -> int:
    steps = [
        ("verify_repo_hygiene", ROOT / "tools" / "verify_repo_hygiene.py"),
        ("verify_foundation", ROOT / "tools" / "verify_foundation.py"),
        ("verify_consistency", ROOT / "tools" / "verify_consistency.py"),
        ("verify_progress_index", ROOT / "tools" / "verify_progress_index.py"),
        ("verify_progress_truth", ROOT / "tools" / "verify_progress_truth.py"),
    ]
    print("PR11_GATE_START")
    print("PR11_GATE_SUMMARY|status=RUNNING|failed=0|degraded=0|skipped=0")
    results: list[dict] = []
    for name, path in steps:
        result = _run_step(name, path)
        results.append(result)
        print(
            "|".join(
                [
                    "PR11_STEP",
                    f"name={name}",
                    f"status={result['status']}",
                    f"exit={result['exit']}",
                ]
            )
        )
        if result.get("stdout"):
            print(result["stdout"].strip())
        if result.get("stderr"):
            print(result["stderr"].strip())

    failed = [r for r in results if r["status"] == "FAIL"]
    degraded = [r for r in results if r["status"] == "DEGRADED"]
    skipped = [r for r in results if r["status"] == "SKIP"]

    summary_status = "FAIL" if failed else "DEGRADED" if degraded else "PASS"
    summary_marker = "|".join(
        [
            "PR11_GATE_SUMMARY",
            f"status={summary_status}",
            f"failed={len(failed)}",
            f"degraded={len(degraded)}",
            f"skipped={len(skipped)}",
        ]
    )
    print(summary_marker)
    print("PR11_GATE_END")
    print(summary_marker)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
