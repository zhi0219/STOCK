from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_logs_dir(cfg: dict) -> Path:
    logging_cfg = cfg.get("logging", {}) or {}
    return ROOT / str(logging_cfg.get("log_dir", "./Logs"))


def _build_events() -> List[dict]:
    now = datetime.now(timezone.utc)
    base = {
        "ts_utc": now.isoformat(),
        "ts_et": now.isoformat(),
        "severity": "low",
        "message": "test event",
        "metrics": {"sample": 1},
        "source": "verify_replay",
        "schema_version": 1,
    }
    return [
        {**base, "event_type": "MOVE", "symbol": "AAPL", "message": "price up"},
        {**base, "event_type": "DATA_STALE", "symbol": "MSFT", "message": "stale data"},
        {**base, "event_type": "DATA_FLAT", "symbol": "AAPL", "message": "flat"},
    ]


def _write_tmp_events(path: Path) -> None:
    events = _build_events()
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _run_replay(script: Path) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(script),
        "--limit",
        "3",
        "--stats",
        "--json",
    ]
    env = None
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)


def main() -> None:
    cfg = load_config()
    logs_dir = get_logs_dir(cfg)
    logs_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = logs_dir / "events__tmp_replay.jsonl"
    replay_script = ROOT / "tools" / "replay_events.py"

    try:
        _write_tmp_events(tmp_path)
        result = _run_replay(replay_script)
        output = (result.stdout or "") + (result.stderr or "")

        if result.returncode != 0:
            print("FAIL: replay_events returned non-zero exit code")
            print(output.rstrip())
            sys.exit(1)

        required_tokens = ["MOVE", "DATA_STALE", "AAPL", "MSFT"]
        missing = [token for token in required_tokens if token not in output]
        if missing:
            print(f"FAIL: replay output missing expected token(s): {', '.join(missing)}")
            print(output.rstrip())
            sys.exit(1)

        print("PASS: replay verified")
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
