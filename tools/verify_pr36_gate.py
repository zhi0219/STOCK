from __future__ import annotations

from pathlib import Path

from tools.compile_check import run_compile_check

ARTIFACTS_DIR = Path("artifacts")


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    compile_targets = ["tools", "alerts.py", "main.py", "quotes.py"]
    payload = run_compile_check(
        targets=compile_targets, artifacts_dir=ARTIFACTS_DIR, force_fail_env="PR36_FORCE_FAIL"
    )

    if payload.get("status") != "PASS":
        print("verify_pr36_gate FAIL")
        exception_summary = payload.get("exception_summary")
        if exception_summary:
            print(f" - {exception_summary}")
        return 1

    print("verify_pr36_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
