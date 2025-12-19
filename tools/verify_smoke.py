from __future__ import annotations

import os
import py_compile
import subprocess
import sys
import time
from pathlib import Path


def tail_file(path: Path, lines: int = 40) -> list[str]:
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    return content[-lines:]


def fail(msg: str, *, log_path: Path | None = None) -> None:
    print(f"FAIL: {msg}")
    if log_path:
        tail = tail_file(log_path)
        if tail:
            print(f"--- tail of {log_path} ---")
            for line in tail:
                print(line)
    print("Next: .\\.venv\\Scripts\\python.exe .\\tools\\verify_smoke.py")
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


def load_config(root: Path) -> dict:
    cfg_path = root / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        import yaml

        with cfg_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


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

    cfg = load_config(root)
    logging_cfg = cfg.get("logging") or {}
    log_path = root / str(logging_cfg.get("log_dir", "./Logs")) / "alerts.log"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)

    proc = subprocess.Popen(
        [sys.executable, str(alerts_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=root,
        env=env,
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
        fail(
            f"ALERTS_START not detected from alerts.py (output: {snippet})",
            log_path=log_path,
        )


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    ensure_dependencies()
    check_files(root)
    compile_targets(root)
    run_alerts_once(root)
    print("PASS: verify_smoke completed")


if __name__ == "__main__":
    main()
