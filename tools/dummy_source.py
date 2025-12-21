from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
DATA_DIR = ROOT / "Data"
KILL_SWITCH = DATA_DIR / "KILL_SWITCH"


def write_heartbeat(path: Path, name: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{name} {ts}\n", encoding="utf-8")


def run(name: str, interval: float) -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    heartbeat_path = LOGS_DIR / f"_tmp_dummy_{name}.txt"
    while True:
        if KILL_SWITCH.exists():
            return 0
        write_heartbeat(heartbeat_path, name)
        time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dummy heartbeat source")
    parser.add_argument("--name", default="source")
    parser.add_argument("--interval", type=float, default=0.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args.name, args.interval)


if __name__ == "__main__":
    sys.exit(main())
