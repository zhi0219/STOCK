from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional

import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
ALERTS_PATH = ROOT / "alerts.py"
INJECTOR_PATH = ROOT / "tools" / "inject_quote.py"

TARGET_COOLDOWN = 300
SYMBOL = "AAPL"
DELTA_PCT = 5.0


class LineWatcher:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, proc: subprocess.Popen, on_line: Callable[[str], None]) -> None:
        def _run() -> None:
            assert proc.stdout is not None
            while not self._stop.is_set():
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                clean = line.rstrip("\n")
                self.lines.append(clean)
                on_line(clean)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def get_dirs(cfg: dict) -> tuple[Path, Path, Path]:
    logging_cfg = cfg.get("logging", {}) or {}
    data_dir = ROOT / str(logging_cfg.get("data_dir", "./Data"))
    logs_dir = ROOT / str(logging_cfg.get("log_dir", "./Logs"))
    risk_cfg = cfg.get("risk_guards", {}) or {}
    kill_switch_path = ROOT / str(risk_cfg.get("kill_switch_path", "./Data/KILL_SWITCH"))
    return data_dir, logs_dir, kill_switch_path


def run_subprocess(args: list[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
    )


def stop_process(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass


def set_cooldown(cfg: dict, target: int) -> dict:
    alerts_cfg = cfg.get("alerts") or {}
    alerts_cfg["cooldown_seconds"] = int(target)
    cfg["alerts"] = alerts_cfg
    return cfg


def verify() -> tuple[bool, str]:
    if not CONFIG_PATH.exists():
        return False, "config.yaml 未找到"

    original_text = CONFIG_PATH.read_text(encoding="utf-8")
    cfg = load_config()
    data_dir, _, kill_switch_path = get_dirs(cfg)
    quotes_path = data_dir / "quotes.csv"

    if kill_switch_path.exists():
        try:
            kill_switch_path.unlink()
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"无法删除 KILL_SWITCH: {exc}"

    try:
        updated_cfg = set_cooldown(dict(cfg), TARGET_COOLDOWN)
        save_config(updated_cfg)

        proc = subprocess.Popen(
            [sys.executable, str(ALERTS_PATH)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        start_event = threading.Event()
        move_event = threading.Event()
        second_move_event = threading.Event()
        cooldown_value: Optional[int] = None

        watcher = LineWatcher()

        def handle_line(line: str) -> None:
            nonlocal cooldown_value
            if "ALERTS_START" in line and "cooldown=" in line:
                try:
                    cooldown_str = line.split("cooldown=")[1].split("s")[0]
                    cooldown_value = int(cooldown_str)
                except Exception:
                    cooldown_value = None
                start_event.set()
            if f"MOVE symbol={SYMBOL.upper()}" in line:
                if not move_event.is_set():
                    move_event.set()
                else:
                    second_move_event.set()

        watcher.start(proc, handle_line)

        if not start_event.wait(timeout=45):
            stop_process(proc)
            watcher.stop()
            return False, "alerts.py 未打印 ALERTS_START"

        if cooldown_value != TARGET_COOLDOWN:
            stop_process(proc)
            watcher.stop()
            return False, f"cooldown 仍为 {cooldown_value}s"

        first_inject = run_subprocess(
            [
                sys.executable,
                str(INJECTOR_PATH),
                "--symbol",
                SYMBOL,
                "--delta-pct",
                str(DELTA_PCT),
            ]
        )
        if first_inject.returncode != 0:
            stop_process(proc)
            watcher.stop()
            return False, f"第一次注入失败: {first_inject.stderr.strip()}"

        second_inject = run_subprocess(
            [
                sys.executable,
                str(INJECTOR_PATH),
                "--symbol",
                SYMBOL,
                "--delta-pct",
                str(DELTA_PCT),
            ]
        )
        if second_inject.returncode != 0:
            stop_process(proc)
            watcher.stop()
            return False, f"第二次注入失败: {second_inject.stderr.strip()}"

        if not move_event.wait(timeout=150):
            stop_process(proc)
            watcher.stop()
            return False, "未捕捉到第一次 MOVE"

        suppressed = not second_move_event.wait(timeout=120)
        stop_process(proc)
        watcher.stop()

        if not suppressed:
            return False, "第二次未抑制 (300s 内又出现 MOVE)"

        if not quotes_path.exists():
            return False, f"找不到 quotes.csv: {quotes_path}"

        return True, f"PASS ✅ cooldown={TARGET_COOLDOWN}s"

    finally:
        CONFIG_PATH.write_text(original_text, encoding="utf-8")
        run_subprocess([sys.executable, str(INJECTOR_PATH), "--cleanup"])


def main() -> None:
    ok, message = verify()
    print(message)
    if not ok:
        print("建议: 检查 .\\Logs\\alerts.log 末尾，确认 cooldown 是否被覆盖，或 config.yaml 是否可读。")
        sys.exit(1)


if __name__ == "__main__":
    main()
