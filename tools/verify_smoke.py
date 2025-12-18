from __future__ import annotations

import py_compile
import subprocess
import sys
import time
from pathlib import Path


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ensure_dependencies() -> None:
    try:
        import yaml  # noqa: F401
        import pandas  # noqa: F401
    except ImportError as e:  # pragma: no cover - runtime guard
        fail(
            "Missing dependency: {}. Please install with PowerShell: "
            ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt".format(
                e.name or "package"
            )
        )


def compile_targets(root: Path) -> None:
    targets = [
        root / "main.py",
        root / "quotes.py",
        root / "alerts.py",
        root / "tools" / "inject_quote.py",
        root / "tools" / "verify_cooldown.py",
        root / "tools" / "verify_smoke.py",
    ]
    for target in targets:
        try:
            py_compile.compile(str(target), doraise=True)
        except Exception as e:  # pragma: no cover - compile failures are direct FAIL
            fail(f"Compile failed for {target}: {e}")


def check_files(root: Path) -> None:
    required = [
        root / "config.yaml",
        root / "tools" / "inject_quote.py",
        root / "tools" / "verify_cooldown.py",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        fail(f"Missing required files: {', '.join(missing)}")


def run_alerts_once(root: Path) -> None:
    alerts_path = root / "alerts.py"
    if not alerts_path.exists():
        fail(f"alerts.py not found at {alerts_path}")

    proc = subprocess.Popen(
        [sys.executable, str(alerts_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=root,
    )

    start_seen = False
    start_time = time.time()
    collected: list[str] = []
    try:
        while time.time() - start_time < 8:
            if proc.stdout is None:
                break
            line = proc.stdout.readline()
            if line:
                collected.append(line.rstrip())
                if "ALERTS_START" in line:
                    start_seen = True
                    break
            elif proc.poll() is not None:
                break
            time.sleep(0.1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not start_seen:
        snippet = " | ".join(collected) if collected else "<no output>"
        fail(f"ALERTS_START not detected from alerts.py (output: {snippet})")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    ensure_dependencies()
    check_files(root)
    compile_targets(root)
    run_alerts_once(root)
    print("PASS: verify_smoke completed")


if __name__ == "__main__":
    main()
