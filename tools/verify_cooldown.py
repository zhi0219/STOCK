from __future__ import annotations
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alerts import alert_key, is_on_cooldown, record_emit


# ----- helpers -----
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


def run_injector(script: Path, symbol: str, cleanup: bool = False) -> None:
    args = [sys.executable, str(script)]
    if cleanup:
        args.append("--cleanup")
    else:
        args.extend(["--symbol", symbol, "--delta-pct", "1.0"])

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        fail(
            f"inject_quote.py failed (code {result.returncode}): {result.stdout}\n{result.stderr}"
        )


def load_quotes(quotes_path: Path) -> Any:
    import pandas as pd

    try:
        return pd.read_csv(quotes_path)
    except Exception as e:  # pragma: no cover - runtime guard
        fail(f"Failed to read {quotes_path}: {e}")
    return None


def ensure_dirs(paths: list[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    ensure_dependencies()
    root = ROOT
    config_path = root / "config.yaml"
    inject_path = root / "tools" / "inject_quote.py"

    if not inject_path.exists():
        fail(f"inject_quote.py not found at {inject_path}")

    original_config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    cfg = read_yaml(config_path)
    alerts_cfg = cfg.get("alerts")
    if alerts_cfg is None:
        alerts_cfg = {}
        cfg["alerts"] = alerts_cfg
    if not isinstance(alerts_cfg, dict):
        fail("alerts section in config.yaml must be a mapping")

    cooldown_seconds = 300
    alerts_cfg["cooldown_seconds"] = cooldown_seconds
    write_yaml(config_path, cfg)

    logging_cfg = cfg.get("logging") or {}
    data_dir = root / str(logging_cfg.get("data_dir", "./Data"))
    logs_dir = root / str(logging_cfg.get("log_dir", "./Logs"))
    ensure_dirs([data_dir, logs_dir])

    quotes_path = data_dir / "quotes.csv"
    alert_state_path = logs_dir / "alert_state.json"

    symbol = "AAPL"

    try:
        # clean state
        if alert_state_path.exists():
            alert_state_path.unlink()

        # inject twice
        run_injector(inject_path, symbol)
        run_injector(inject_path, symbol)

        if not quotes_path.exists():
            fail(f"quotes.csv not found at {quotes_path}")

        df = load_quotes(quotes_path)
        cols_lower = {c.lower(): c for c in df.columns}
        sym_col = cols_lower.get("symbol")
        price_col = cols_lower.get("price")
        ts_col = cols_lower.get("ts_utc")
        source_col = cols_lower.get("source")

        if not sym_col or not price_col or not ts_col:
            fail("quotes.csv is missing required columns ts_utc/symbol/price")

        df = df[[ts_col, sym_col, price_col] + ([source_col] if source_col else [])].copy()
        df.rename(columns={ts_col: "ts_utc", sym_col: "symbol", price_col: "price"}, inplace=True)
        df["symbol"] = df["symbol"].astype(str).str.upper()

        injected = df[df.get("source", "") == "SELF_TEST_INJECT"] if source_col else df
        injected = injected[injected["symbol"] == symbol]
        if len(injected) < 4:
            fail("Not enough injected rows found to verify cooldown (need at least 4)")

        injected = injected.sort_values("ts_utc")
        first_pair = injected.head(2)
        second_pair = injected.iloc[2:4]

        try:
            prev1, now1 = float(first_pair.iloc[0]["price"]), float(first_pair.iloc[1]["price"])
            prev2, now2 = float(second_pair.iloc[0]["price"]), float(second_pair.iloc[1]["price"])
        except Exception as e:
            fail(f"Failed to parse prices from injected rows: {e}")

        move1 = (now1 - prev1) / prev1 * 100 if prev1 else 0.0
        move2 = (now2 - prev2) / prev2 * 100 if prev2 else 0.0
        if abs(move1) < 0.01 or abs(move2) < 0.01:
            fail("Injected moves are too small; expected non-zero MOVE signals")

        # record first emission and verify cooldown prevents second
        key = alert_key("MOVE", symbol)
        state: dict[str, Any] = {}
        now_epoch = time.time()
        record_emit(key, state, alert_state_path, now_epoch=now_epoch)
        suppressed = is_on_cooldown(
            key, int(alerts_cfg.get("cooldown_seconds", cooldown_seconds)), state, now_epoch=now_epoch + 1
        )
        if not suppressed:
            fail("Cooldown did not suppress second MOVE within 300s window")

        print("PASS: cooldown verified (second MOVE suppressed within 300s)")
    finally:
        # cleanup
        try:
            run_injector(inject_path, symbol, cleanup=True)
        except Exception:
            pass
        try:
            if alert_state_path.exists():
                alert_state_path.unlink()
        except Exception:
            pass
        if original_config_text:
            config_path.write_text(original_config_text, encoding="utf-8")
        else:
            config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
