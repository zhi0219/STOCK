from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
INJECT_SOURCE = "SELF_TEST_INJECT"


@dataclass
class Quote:
    ts_utc: str
    symbol: str
    price: float
    source: str = INJECT_SOURCE


# ---------- helpers ----------
def load_config() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_data_dir(cfg: dict) -> Path:
    logging_cfg = cfg.get("logging", {}) or {}
    return ROOT / str(logging_cfg.get("data_dir", "./Data"))


def ensure_quotes_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                has_content = f.read(1)
        except Exception:
            has_content = True
        if has_content:
            return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ts_utc", "symbol", "price", "source"])


def read_quotes(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=["ts_utc", "symbol", "price", "source"])


def find_base_price(df: pd.DataFrame, symbol: str) -> float:
    symbol_upper = symbol.upper()
    if not df.empty:
        cols_lower = {c.lower(): c for c in df.columns}
        sym_col = cols_lower.get("symbol")
        price_col = cols_lower.get("price")
        if sym_col and price_col:
            try:
                cols = [sym_col, price_col]
                source_col = cols_lower.get("source")
                if source_col:
                    cols.append(source_col)

                df_norm = df[cols].copy()
                df_norm.rename(columns={sym_col: "symbol", price_col: "price"}, inplace=True)
                df_norm["symbol"] = df_norm["symbol"].astype(str).str.upper()
                df_norm = df_norm[df_norm.get("symbol") == symbol_upper]
                df_norm = df_norm[pd.to_numeric(df_norm.get("price"), errors="coerce").notna()]
                if source_col:
                    df_norm = df_norm[df_norm[source_col] != INJECT_SOURCE]
                if not df_norm.empty:
                    return float(df_norm.tail(1)["price"].iloc[0])
            except Exception:
                pass
    return 100.0


def build_quotes(symbol: str, base_price: float, delta_pct: float) -> list[Quote]:
    now = datetime.now(timezone.utc)
    prev_ts = (now - timedelta(seconds=1)).isoformat(timespec="seconds")
    now_ts = now.isoformat(timespec="seconds")

    prev_price = base_price
    new_price = base_price * (1.0 + delta_pct / 100.0)

    return [
        Quote(ts_utc=prev_ts, symbol=symbol.upper(), price=prev_price),
        Quote(ts_utc=now_ts, symbol=symbol.upper(), price=new_price),
    ]


def append_quotes(path: Path, quotes: list[Quote], dry_run: bool) -> None:
    if dry_run:
        print("[DRY-RUN] Would append rows to", path)
        for q in quotes:
            print(f"  {q.ts_utc}, {q.symbol}, {q.price:.6f}, {q.source}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists or path.stat().st_size == 0:
            w.writerow(["ts_utc", "symbol", "price", "source"])
        for q in quotes:
            w.writerow([q.ts_utc, q.symbol, f"{q.price:.6f}", q.source])


# ---------- cleanup ----------
def cleanup_injected(path: Path, dry_run: bool) -> None:
    if not path.exists():
        print(f"quotes.csv not found: {path}")
        return

    df = read_quotes(path)
    if "source" not in {c.lower() for c in df.columns}:
        print("No source column detected; nothing to clean up.")
        return

    cols_lower = {c.lower(): c for c in df.columns}
    source_col = cols_lower.get("source")
    df_clean = df[df[source_col] != INJECT_SOURCE]

    removed = len(df) - len(df_clean)
    if removed == 0:
        print("No injected rows found; file left unchanged.")
        return

    if dry_run:
        print(f"[DRY-RUN] Would remove {removed} injected rows from {path}")
        return

    # rewrite safely
    tmp_path = path.with_suffix(".tmp")
    df_clean.to_csv(tmp_path, index=False)
    tmp_path.replace(path)
    print(f"Removed {removed} injected rows from {path}")


# ---------- main ----------
def main() -> None:
    parser = argparse.ArgumentParser(description="Inject synthetic quotes for MOVE self-test.")
    parser.add_argument("--symbol", default="AAPL", help="Symbol to inject (default: AAPL)")
    parser.add_argument("--delta-pct", type=float, default=1.0, help="Move percentage to simulate (default: 1.0)")
    parser.add_argument("--cleanup", action="store_true", help="Remove previously injected rows only")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    args = parser.parse_args()

    cfg = load_config()
    data_dir = get_data_dir(cfg)
    quotes_path = data_dir / "quotes.csv"

    if args.cleanup:
        cleanup_injected(quotes_path, args.dry_run)
        return

    ensure_quotes_csv(quotes_path)
    existing = read_quotes(quotes_path)
    base_price = find_base_price(existing, args.symbol)
    quotes = build_quotes(args.symbol, base_price, args.delta_pct)
    append_quotes(quotes_path, quotes, args.dry_run)

    if not args.dry_run:
        print(
            f"Injected {len(quotes)} rows for {args.symbol.upper()} into {quotes_path} "
            f"with delta {args.delta_pct:+.2f}% (base={base_price:.6f})"
        )


if __name__ == "__main__":
    main()
