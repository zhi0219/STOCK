from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("缺少依赖 pyyaml。请先运行：pip install pyyaml", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
LOG_DIR = ROOT / "Logs"  # 你现在的文件夹叫 Logs（首字母大写）


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"找不到 config.yaml：{CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "run.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    cfg = load_config()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mode = cfg.get("mode", "UNKNOWN")
    watchlist = cfg.get("watchlist", {})

    msg = f"[{now}] START mode={mode} watchlist={watchlist}"
    print(msg)
    write_log(msg)

    if mode != "READ_ONLY":
        print("WARNING：当前教程默认 READ_ONLY（只读）。", file=sys.stderr)

    print("✅ 配置读取成功。下一步我们会加入“读取行情”（仍只读）。")


if __name__ == "__main__":
    main()
