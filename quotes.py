from __future__ import annotations

import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

try:
    import yfinance as yf
except ImportError:
    print("缺少依赖 yfinance。请先运行：pip install yfinance pandas", file=sys.stderr)
    raise


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


@dataclass
class Quote:
    ts_utc: str
    symbol: str
    price: float
    source: str


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"找不到 config.yaml：{CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_log(log_dir: Path, line: str) -> None:
    ensure_dir(log_dir)
    with (log_dir / "run.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_dirs(cfg: dict) -> tuple[Path, Path]:
    logging_cfg = cfg.get("logging", {})
    log_dir = ROOT / str(logging_cfg.get("log_dir", "./Logs"))
    data_dir = ROOT / str(logging_cfg.get("data_dir", "./Data"))
    return log_dir, data_dir


def fetch_last_price(symbol: str) -> Optional[float]:
    """
    取“最新价”：先 fast_info，失败则用 1分钟K线最后一个 close
    """
    t = yf.Ticker(symbol)

    try:
        fi = getattr(t, "fast_info", None)
        if fi:
            p = fi.get("last_price") or fi.get("lastPrice")
            if p is not None:
                return float(p)
    except Exception:
        pass

    try:
        hist = t.history(period="1d", interval="1m", prepost=True)
        if hist is None or hist.empty:
            return None
        last_close = hist["Close"].dropna()
        if last_close.empty:
            return None
        return float(last_close.iloc[-1])
    except Exception:
        return None


def append_quotes_csv(data_dir: Path, quotes: list[Quote]) -> Path:
    ensure_dir(data_dir)
    out = data_dir / "quotes.csv"
    file_exists = out.exists()

    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["ts_utc", "symbol", "price", "source"])
        for q in quotes:
            w.writerow([q.ts_utc, q.symbol, q.price, q.source])

    return out


def main() -> None:
    cfg = load_config()
    log_dir, data_dir = get_dirs(cfg)

    wl = cfg.get("watchlist", {})
    symbols = list(wl.get("stocks", [])) + list(wl.get("etfs", []))
    if not symbols:
        raise ValueError("watchlist 为空：请在 config.yaml 里设置 stocks/etfs")

    # 每隔多少秒拉一次（没写就默认 60s）
    poll_seconds = int(cfg.get("poll_seconds", 60))
    stale_seconds = int(cfg.get("alerts", {}).get("data_stale_seconds", 30))

    last_good_ts = time.time()

    write_log(log_dir, f"[{now_utc_iso()}] QUOTES_START symbols={symbols} poll_seconds={poll_seconds}")

    while True:
        ts = now_utc_iso()
        quotes: list[Quote] = []
        failed: list[str] = []

        for sym in symbols:
            p = fetch_last_price(sym)
            if p is None:
                failed.append(sym)
            else:
                quotes.append(Quote(ts_utc=ts, symbol=sym, price=p, source="yfinance"))

        if quotes:
            last_good_ts = time.time()
            out = append_quotes_csv(data_dir, quotes)
            print(f"[{ts}] ✅ 写入 {len(quotes)} 条报价 -> {out}")
            write_log(log_dir, f"[{ts}] QUOTES_OK n={len(quotes)} file={out.name}")

        if failed:
            msg = f"[{ts}] ⚠️ 报价获取失败：{failed}"
            print(msg)
            write_log(log_dir, msg)

        if (time.time() - last_good_ts) > stale_seconds:
            msg = f"[{now_utc_iso()}] 🛑 DATA_STALE 超过 {stale_seconds}s 未获取到有效报价（只报警，不做任何动作）"
            print(msg)
            write_log(log_dir, msg)
            last_good_ts = time.time()

        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
