from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, List


ROOT = Path(__file__).resolve().parents[1]

COOLDOWN_SECONDS = 300
START_TIMEOUT = 30.0
LOG_TAIL = 80


def tail_file(path: Path, lines: int = LOG_TAIL) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:]
    except Exception:
        return []


def fail(msg: str, *, log_path: Path | None = None) -> None:
    print(f"FAIL: {msg}")
    if log_path:
        tail = tail_file(log_path)
        if tail:
            print(f"--- tail of {log_path} (last {LOG_TAIL} lines) ---")
            for line in tail:
                print(line)
    print("Next: .\\.venv\\Scripts\\python.exe .\\tools\\verify_cooldown.py")
    sys.exit(1)


def ensure_dependencies() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:  # pragma: no cover - runtime guard
        print("你没有用 Windows venv 运行。请用 .\\.venv\\Scripts\\python.exe tools/verify_cooldown.py")
        sys.exit(1)
    try:
        import pandas  # noqa: F401
    except ImportError as e:  # pragma: no cover - runtime guard
        fail(
            f"Missing dependency: {e.name or 'package'}. "
            "Install with: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        )


def read_yaml(path: Path) -> dict:
    import yaml

    if not path.exists():
        fail(f"config.yaml not found at {path}")
    with path.open("r", encoding="utf-8") as f:
        try:
            return yaml.safe_load(f) or {}
        except Exception as e:  # pragma: no cover - config parse issues
            fail(f"Failed to parse {path}: {e}")
    return {}


def write_yaml(path: Path, data: dict) -> None:
    import yaml

    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def ensure_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def run_injector(script: Path, symbol: str, *, delta_pct: float, cleanup: bool = False, log_path: Path | None = None) -> None:
    args = [sys.executable, str(script)]
    if cleanup:
        args.append("--cleanup")
    else:
        args.extend(["--symbol", symbol, "--delta-pct", f"{delta_pct}"])

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        fail(
            f"inject_quote.py failed (code {result.returncode}): {result.stdout}\n{result.stderr}",
            log_path=log_path,
        )


def select_symbol(cfg: dict) -> str:
    watchlist = cfg.get("watchlist")
    if isinstance(watchlist, str):
        symbols = [s.strip().upper() for s in watchlist.split(",") if s.strip()]
    elif isinstance(watchlist, list):
        symbols = [str(s).strip().upper() for s in watchlist if str(s).strip()]
    elif isinstance(watchlist, dict):
        symbols = []
        for key in ("stocks", "etfs"):
            val = watchlist.get(key, [])
            if isinstance(val, list):
                symbols.extend([str(s).strip().upper() for s in val if str(s).strip()])
    else:
        symbols = []

    if "AAPL" in symbols:
        return "AAPL"
    if symbols:
        return symbols[0]
    return "AAPL"


def start_alerts(root: Path) -> tuple[subprocess.Popen[str], List[str]]:
    cmd = [sys.executable, "-u", str(root / "alerts.py")]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    proc = subprocess.Popen(
        cmd,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    lines: List[str] = []

    def _reader() -> None:
        assert proc.stdout
        for line in proc.stdout:
            lines.append(line.rstrip())

    threading.Thread(target=_reader, daemon=True).start()
    return proc, lines


def wait_for_line(
    lines: List[str], predicate: Callable[[str], bool], timeout: float, *, start_index: int
) -> tuple[str | None, int]:
    start = time.monotonic()
    idx = start_index
    while time.monotonic() - start < timeout:
        while idx < len(lines):
            line = lines[idx]
            idx += 1
            if predicate(line):
                return line, idx
        time.sleep(0.1)
    return None, idx


def stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def delete_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except Exception:
        return


def main() -> None:
    ensure_dependencies()

    root = ROOT
    config_path = root / "config.yaml"
    inject_path = root / "tools" / "inject_quote.py"

    if not inject_path.exists():
        fail(f"inject_quote.py not found at {inject_path}")

    original_config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    cfg = read_yaml(config_path)

    # temp overrides
    alerts_cfg = cfg.get("alerts") or {}
    if not isinstance(alerts_cfg, dict):
        fail("alerts section in config.yaml must be a mapping")
    cfg["alerts"] = alerts_cfg
    alerts_cfg["cooldown_seconds"] = COOLDOWN_SECONDS

    try:
        minute_move_pct = float(alerts_cfg.get("minute_move_pct", 1.0) or 1.0)
    except Exception:
        minute_move_pct = 1.0
    delta_pct = max(5.0, minute_move_pct * 3.0)

    # keep poll_seconds small for fast verification but restore later
    poll_seconds = int(cfg.get("poll_seconds", 60) or 60)
    if poll_seconds > 15:
        cfg["poll_seconds"] = 5
        poll_seconds = 5

    write_yaml(config_path, cfg)

    logging_cfg = cfg.get("logging") or {}
    data_dir = root / str(logging_cfg.get("data_dir", "./Data"))
    logs_dir = root / str(logging_cfg.get("log_dir", "./Logs"))
    log_path = logs_dir / "alerts.log"

    ensure_dirs([data_dir, logs_dir])

    alert_state_path = logs_dir / "alert_state.json"

    risk_cfg = cfg.get("risk_guards") or {}
    kill_switch_path = root / str(risk_cfg.get("kill_switch_path", "./Data/KILL_SWITCH"))
    delete_if_exists(kill_switch_path)
    delete_if_exists(alert_state_path)

    symbol = select_symbol(cfg)

    # start alerts loop
    proc, lines = start_alerts(root)

    try:
        start_line, idx = wait_for_line(
            lines, lambda l: "ALERTS_START" in l, timeout=START_TIMEOUT, start_index=0
        )
        if not start_line:
            fail("Did not capture ALERTS_START from alerts.py", log_path=log_path)
        print(start_line)
        if f"cooldown={COOLDOWN_SECONDS}" not in start_line:
            fail(f"ALERTS_START reported wrong cooldown: {start_line}", log_path=log_path)

        # first injection
        run_injector(inject_path, symbol, delta_pct=delta_pct, log_path=log_path)

        first_move, idx = wait_for_line(
            lines,
            lambda l: f"MOVE symbol={symbol.upper()}" in l,
            timeout=max(30.0, poll_seconds * 2),
            start_index=idx,
        )
        if not first_move:
            fail("Did not observe first MOVE after injection", log_path=log_path)
        print(first_move)

        time.sleep(2)
        run_injector(inject_path, symbol, delta_pct=delta_pct, log_path=log_path)

        suppressed_line, _ = wait_for_line(
            lines,
            lambda l: f"MOVE symbol={symbol.upper()}" in l,
            timeout=max(10.0, poll_seconds * 1.5),
            start_index=idx,
        )
        if suppressed_line:
            fail(f"Cooldown did not suppress second MOVE: {suppressed_line}", log_path=log_path)

        print(
            "PASS: cooldown verified (cooldown=300s, first MOVE observed, second suppressed)"
        )
    finally:
        stop_process(proc)
        try:
            run_injector(inject_path, symbol, cleanup=True, delta_pct=delta_pct, log_path=log_path)
        except Exception:
            pass
        delete_if_exists(alert_state_path)
        delete_if_exists(kill_switch_path)
        if original_config_text:
            config_path.write_text(original_config_text, encoding="utf-8")
        else:
            config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
