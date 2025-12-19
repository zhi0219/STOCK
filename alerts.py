from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


# ---------- time helpers ----------
def now_stamps() -> Tuple[str, str, str]:
    """Return (utc_iso, local_iso, local_tz_name)."""
    u = datetime.now(timezone.utc).isoformat(timespec="seconds")
    local_dt = datetime.now(timezone.utc).astimezone()
    local = local_dt.isoformat(timespec="seconds")
    tzname = local_dt.tzname() or "LOCAL"
    return u, local, tzname


# ---------- io helpers ----------
def load_config() -> Dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_alert_state(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_alert_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp.replace(path)


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def make_event(
    alert_type: str,
    symbol: str,
    severity: str,
    message: str,
    metrics: Dict[str, Any],
    *,
    source: str,
) -> Dict[str, Any]:
    ts_utc = datetime.now(timezone.utc)
    ts_et = ts_utc.astimezone(ZoneInfo("America/New_York"))
    sym = symbol if symbol else "__GLOBAL__"
    return {
        "ts_utc": ts_utc.isoformat(timespec="seconds"),
        "ts_et": ts_et.isoformat(timespec="seconds"),
        "event_type": alert_type,
        "symbol": sym,
        "severity": severity,
        "message": message,
        "metrics": metrics,
        "source": source,
    }


def emit_event(
    event: Dict[str, Any],
    message: str,
    *,
    alerts_log: Path,
    events_log: Path,
    learning_card: Optional[Dict[str, str]] = None,
) -> None:
    try:
        print(message)
    except Exception:
        pass

    try:
        append_line(alerts_log, message)
    except Exception as e:
        try:
            print(f"[WARN] failed to append alerts.log: {e}")
        except Exception:
            pass

    try:
        events_log.parent.mkdir(parents=True, exist_ok=True)
        with events_log.open("a", encoding="utf-8") as f:
            json.dump(event, f, ensure_ascii=False)
            f.write("\n")
    except Exception as e:
        try:
            print(f"[WARN] failed to append events.jsonl: {e}")
        except Exception:
            pass

    if learning_card is not None:
        try:
            append_learning_card(
                learning_card["path"],
                alert_type=learning_card["alert_type"],
                symbol=learning_card["symbol"],
                facts=learning_card["facts"],
                hypotheses=learning_card["hypotheses"],
                checks=learning_card["checks"],
                concepts=learning_card["concepts"],
            )
        except Exception as e:
            try:
                print(f"[WARN] failed to append learning card: {e}")
            except Exception:
                pass


def alert_key(alert_type: str, symbol: str) -> str:
    sym = symbol.strip().upper() if symbol else "__GLOBAL__"
    if sym == "-":
        sym = "__GLOBAL__"
    return f"{alert_type}|{sym}"


def is_on_cooldown(
    key: str, cooldown_seconds: int, state: Dict[str, Any], *, now_epoch: Optional[float] = None
) -> bool:
    now_epoch = now_epoch if now_epoch is not None else time.time()
    last = state.get(key, {}).get("last_emit_epoch")
    return bool(last) and (now_epoch - float(last) < cooldown_seconds)


def record_emit(key: str, state: Dict[str, Any], path: Path, *, now_epoch: Optional[float] = None) -> None:
    now_epoch = now_epoch if now_epoch is not None else time.time()
    state[key] = {"last_emit_epoch": float(now_epoch)}
    save_alert_state(path, state)


def append_learning_card(
    path: Path,
    alert_type: str,
    symbol: str,
    facts: str,
    hypotheses: str,
    checks: str,
    concepts: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    utc_s, local_s, tzname = now_stamps()
    with path.open("a", encoding="utf-8") as f:
        f.write("\n---\n")
        f.write(f"## [{alert_type}] {symbol}\n\n")
        f.write(f"- time_utc: `{utc_s}`\n")
        f.write(f"- time_local({tzname}): `{local_s}`\n\n")
        f.write("**Facts**\n")
        f.write(f"{facts.strip()}\n\n")
        f.write("**Hypotheses**\n")
        f.write(f"{hypotheses.strip()}\n\n")
        f.write("**Checks**\n")
        f.write(f"{checks.strip()}\n\n")
        f.write("**Concepts**\n")
        f.write(f"{concepts.strip()}\n")


def write_status(
    path: Path,
    *,
    ts_utc: datetime,
    ts_et: datetime,
    quotes_path: Path,
    quotes_file_age_s: Optional[float],
    last_rows: Optional[int],
    last_prices: Optional[Dict[str, float]],
    last_alert_ts: Optional[str],
) -> None:
    try:
        payload = {
            "ts_utc": ts_utc.isoformat(timespec="seconds"),
            "ts_et": ts_et.isoformat(timespec="seconds"),
            "quotes_path": str(quotes_path),
            "quotes_file_age_s": quotes_file_age_s,
            "last_rows": last_rows,
            "last_prices": last_prices,
            "last_alert_ts": last_alert_ts,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e:
        try:
            print(f"[WARN] failed to write status.json: {e}")
        except Exception:
            pass


def safe_read_csv(path: Path, retries: int = 3, sleep_s: float = 0.25) -> pd.DataFrame:
    last_err: Optional[Exception] = None
    for _ in range(retries):
        try:
            return pd.read_csv(path)
        except Exception as e:
            last_err = e
            time.sleep(sleep_s)
    raise last_err or RuntimeError("read_csv failed")


def as_upper_symbol_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.upper()


@dataclass
class FlatState:
    run_len: int = 0
    last_price: Optional[float] = None
    last_ts: Optional[pd.Timestamp] = None


# ---------- main ----------
def main() -> None:
    cfg = load_config()

    # paths
    logging_cfg = cfg.get("logging", {}) or {}
    data_dir = ROOT / str(logging_cfg.get("data_dir", ".\\Data"))
    logs_dir = ROOT / str(logging_cfg.get("log_dir", ".\\Logs"))

    quotes_path = data_dir / "quotes.csv"
    alerts_log = logs_dir / "alerts.log"
    events_log = logs_dir / "events.jsonl"
    status_path = logs_dir / "status.json"
    learning_cards_path = data_dir / "learning_cards.md"
    alert_state_path = logs_dir / "alert_state.json"

    # config values
    alerts_cfg = cfg.get("alerts", {}) or {}
    poll_seconds = int(cfg.get("poll_seconds", 60))
    minute_thr = float(alerts_cfg.get("minute_move_pct", 1.0))
    flat_repeats = int(alerts_cfg.get("flat_repeats", cfg.get("flat_repeats", 10)))
    stale_seconds = int(
        alerts_cfg.get(
            "stale_seconds",
            cfg.get("stale_seconds", max(3 * poll_seconds, 180)),
        )
    )
    try:
        cooldown_seconds = int(alerts_cfg.get("cooldown_seconds"))
    except Exception:
        cooldown_seconds = poll_seconds
    if cooldown_seconds <= 0:
        cooldown_seconds = poll_seconds
    debug_enabled = bool(alerts_cfg.get("debug", False))

    risk_cfg = cfg.get("risk_guards", {}) or {}
    kill_switch_path = ROOT / str(risk_cfg.get("kill_switch_path", "./Data/KILL_SWITCH"))

    alert_state = load_alert_state(alert_state_path)

    last_alert_ts: Optional[str] = None

    def flush_status(
        quotes_file_age_s: Optional[float],
        last_rows: Optional[int],
        last_prices: Optional[Dict[str, float]],
    ) -> None:
        ts_utc_dt = datetime.now(timezone.utc)
        ts_et_dt = ts_utc_dt.astimezone(ZoneInfo("America/New_York"))
        write_status(
            status_path,
            ts_utc=ts_utc_dt,
            ts_et=ts_et_dt,
            quotes_path=quotes_path,
            quotes_file_age_s=quotes_file_age_s,
            last_rows=last_rows,
            last_prices=last_prices,
            last_alert_ts=last_alert_ts,
        )

    watchlist = cfg.get("watchlist")
    if isinstance(watchlist, str):
        watchlist_set = {x.strip().upper() for x in watchlist.split(",") if x.strip()}
    elif isinstance(watchlist, list):
        watchlist_set = {str(x).strip().upper() for x in watchlist if str(x).strip()}
    else:
        watchlist_set = None  # no filter

    # startup banner
    utc_s, local_s, tzname = now_stamps()
    start_line = (
        f"[{utc_s} | {local_s} {tzname}] ALERTS_START "
        f"thr={minute_thr}% poll={poll_seconds}s flat={flat_repeats} stale={stale_seconds}s "
        f"cooldown={cooldown_seconds}s debug={debug_enabled} quotes={quotes_path}"
    )
    print(start_line)
    append_line(alerts_log, start_line)

    # file health state
    last_file_mtime: float = 0.0

    # per-symbol state
    flat_state: Dict[str, FlatState] = {}

    while True:
        quotes_file_age_s: Optional[float] = None
        last_rows: Optional[int] = None
        last_prices: Optional[Dict[str, float]] = None

        if kill_switch_path.exists():
            utc_s, local_s, tzname = now_stamps()
            msg = f"[{utc_s} | {local_s} {tzname}] KILL_SWITCH detected at {kill_switch_path}, exiting"
            emit_event(
                make_event(
                    "KILL_SWITCH",
                    "__GLOBAL__",
                    "high",
                    msg,
                    metrics={"path": str(kill_switch_path)},
                    source="quotes.csv",
                ),
                msg,
                alerts_log=alerts_log,
                events_log=events_log,
            )
            return

        # --- DATA_MISSING ---
        if not quotes_path.exists():
            now_epoch = time.time()
            key = alert_key("DATA_MISSING", "__GLOBAL__")
            if not is_on_cooldown(key, cooldown_seconds, alert_state, now_epoch=now_epoch):
                utc_s, local_s, tzname = now_stamps()
                msg = f"[{utc_s} | {local_s} {tzname}] ⚠️ DATA_MISSING symbol=- quotes.csv not found: {quotes_path}"
                event = make_event(
                    "DATA_MISSING",
                    "__GLOBAL__",
                    "med",
                    msg,
                    metrics={"path": str(quotes_path)},
                    source="quotes.csv",
                )
                emit_event(
                    event,
                    msg,
                    alerts_log=alerts_log,
                    events_log=events_log,
                    learning_card={
                        "path": learning_cards_path,
                        "alert_type": "DATA_MISSING",
                        "symbol": "-",
                        "facts": f"- quotes.csv 不存在：`{quotes_path}`",
                        "hypotheses": "- quotes.py 没运行 / 路径不对 / Data 目录被改名",
                        "checks": "- `dir .\\Data` 看看有没有 quotes.csv\n- 重新运行：`python .\\quotes.py`",
                        "concepts": "- DATA_MISSING：数据文件缺失（不是行情波动）。",
                    },
                )
                last_alert_ts = event["ts_utc"]
                record_emit(key, alert_state, alert_state_path, now_epoch=now_epoch)
            flush_status(quotes_file_age_s, last_rows, last_prices)
            time.sleep(poll_seconds)
            continue

        # --- DATA_STALE (mtime based) ---
        try:
            mtime = quotes_path.stat().st_mtime
        except Exception:
            flush_status(quotes_file_age_s, last_rows, last_prices)
            time.sleep(poll_seconds)
            continue

        quotes_file_age_s = time.time() - mtime

        if last_file_mtime == 0.0:
            last_file_mtime = mtime
        else:
            if mtime != last_file_mtime:
                last_file_mtime = mtime
            else:
                # unchanged mtime
                age = time.time() - mtime
                quotes_file_age_s = age
                if age >= stale_seconds:
                    now_epoch = time.time()
                    key = alert_key("DATA_STALE", "__GLOBAL__")
                    if not is_on_cooldown(key, cooldown_seconds, alert_state, now_epoch=now_epoch):
                        utc_s, local_s, tzname = now_stamps()
                        msg = (
                            f"[{utc_s} | {local_s} {tzname}] ⚠️ DATA_STALE symbol=- "
                            f"quotes.csv mtime unchanged >= {stale_seconds}s"
                        )
                        event = make_event(
                            "DATA_STALE",
                            "__GLOBAL__",
                            "med",
                            msg,
                            metrics={"stale_age_s": age, "threshold": stale_seconds},
                            source="quotes.csv",
                        )
                        emit_event(
                            event,
                            msg,
                            alerts_log=alerts_log,
                            events_log=events_log,
                            learning_card={
                                "path": learning_cards_path,
                                "alert_type": "DATA_STALE",
                                "symbol": "-",
                                "facts": f"- quotes.csv 超过 {stale_seconds}s 没有更新（mtime 未变化）。",
                                "hypotheses": "- quotes.py 停了 / 网络断了 / 数据源卡住 / 进程挂起",
                                "checks": "- quotes.py 窗口是否还在输出？\n- `dir .\\Data\\quotes.csv` 看修改时间\n- 先重启 quotes：Ctrl+C → `python .\\quotes.py`",
                                "concepts": "- DATA_STALE：数据流健康检查，和市场是否波动是两回事。",
                            },
                        )
                        last_alert_ts = event["ts_utc"]
                        record_emit(key, alert_state, alert_state_path, now_epoch=now_epoch)

        # --- read csv (with retry) ---
        try:
            df = safe_read_csv(quotes_path)
        except Exception as e:
            now_epoch = time.time()
            key = alert_key("READ_FAIL", "__GLOBAL__")
            if is_on_cooldown(key, cooldown_seconds, alert_state, now_epoch=now_epoch):
                flush_status(quotes_file_age_s, last_rows, last_prices)
                time.sleep(poll_seconds)
                continue
            utc_s, local_s, tzname = now_stamps()
            msg = f"[{utc_s} | {local_s} {tzname}] ⚠️ READ_FAIL symbol=- {type(e).__name__}: {e}"
            event = make_event(
                "READ_FAIL",
                "__GLOBAL__",
                "med",
                msg,
                metrics={"error": str(e)},
                source="quotes.csv",
            )
            emit_event(event, msg, alerts_log=alerts_log, events_log=events_log)
            last_alert_ts = event["ts_utc"]
            record_emit(key, alert_state, alert_state_path, now_epoch=now_epoch)
            flush_status(quotes_file_age_s, last_rows, last_prices)
            time.sleep(poll_seconds)
            continue

        if debug_enabled:
            age = time.time() - mtime
            utc_s, _, _ = now_stamps()
            print(
                f"[{utc_s}] DEBUG FILE age={age:.1f}s stale_thr={stale_seconds}s rows={len(df)}"
            )

        if df.empty:
            last_rows = 0
            flush_status(quotes_file_age_s, last_rows, last_prices)
            time.sleep(poll_seconds)
            continue

        # required columns
        cols_lower = {c.lower(): c for c in df.columns}
        sym_col = cols_lower.get("symbol")
        price_col = cols_lower.get("price")
        ts_col = cols_lower.get("ts_utc")

        if not sym_col or not price_col or not ts_col:
            # silently wait; file schema not ready
            flush_status(quotes_file_age_s, last_rows, last_prices)
            time.sleep(poll_seconds)
            continue

        # normalize
        df = df[[ts_col, sym_col, price_col]].copy()
        df.rename(columns={ts_col: "ts_utc", sym_col: "symbol", price_col: "price"}, inplace=True)

        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")
        df["symbol"] = as_upper_symbol_series(df["symbol"])
        df["price"] = pd.to_numeric(df["price"], errors="coerce")

        df = df.dropna(subset=["ts_utc", "symbol", "price"]).sort_values("ts_utc")

        if watchlist_set is not None:
            df = df[df["symbol"].isin(watchlist_set)]

        if df.empty:
            last_rows = 0
            flush_status(quotes_file_age_s, last_rows, last_prices)
            time.sleep(poll_seconds)
            continue

        last_rows = int(len(df))
        last_prices = {}
        latest_by_symbol = df.sort_values("ts_utc").drop_duplicates("symbol", keep="last")
        for _, row in latest_by_symbol.iterrows():
            try:
                last_prices[str(row["symbol"])] = float(row["price"])
            except Exception:
                continue

        # --- per symbol: MOVE + DATA_FLAT ---
        for sym, g in df.groupby("symbol"):
            g = g.sort_values("ts_utc")
            if len(g) < 2:
                continue

            last2 = g.tail(2)
            now_ts = last2.iloc[1]["ts_utc"]
            prev = float(last2.iloc[0]["price"])
            now = float(last2.iloc[1]["price"])

            st = flat_state.get(sym) or FlatState()

            # avoid re-processing same latest timestamp
            if st.last_ts is not None and pd.Timestamp(now_ts) == pd.Timestamp(st.last_ts):
                flat_state[sym] = st
                continue

            # DATA_FLAT run length (count consecutive updates with same price)
            if st.last_price is None:
                st.run_len = 1
            else:
                if abs(now - float(st.last_price)) < 1e-12:
                    st.run_len += 1
                else:
                    st.run_len = 1

            st.last_price = now
            st.last_ts = pd.Timestamp(now_ts)
            flat_state[sym] = st

            if st.run_len == flat_repeats:
                now_epoch = time.time()
                key = alert_key("DATA_FLAT", sym)
                if not is_on_cooldown(key, cooldown_seconds, alert_state, now_epoch=now_epoch):
                    utc_s, local_s, tzname = now_stamps()
                    msg = (
                        f"[{utc_s} | {local_s} {tzname}] ⚠️ DATA_FLAT symbol={sym} "
                        f"unchanged run_len={st.run_len} price={now:.6f} last_ts={now_ts.isoformat(timespec='seconds')}"
                    )
                    event = make_event(
                        "DATA_FLAT",
                        sym,
                        "low",
                        msg,
                        metrics={
                            "run_len": st.run_len,
                            "price": now,
                            "threshold": flat_repeats,
                            "last_ts": now_ts.isoformat(timespec="seconds"),
                        },
                        source="quotes.csv",
                    )
                    emit_event(
                        event,
                        msg,
                        alerts_log=alerts_log,
                        events_log=events_log,
                        learning_card={
                            "path": learning_cards_path,
                            "alert_type": "DATA_FLAT",
                            "symbol": sym,
                            "facts": f"- {sym} 价格连续 {flat_repeats} 次更新未变化\n- price={now:.6f}\n- last_ts={now_ts.isoformat(timespec='seconds')}",
                            "hypotheses": "- 周末/盘后正常冻结\n- 数据源只给昨收/最后成交\n- 你拿到的是缓存价",
                            "checks": "- 看 SPY 是否也冻结\n- 检查是否周末/盘后\n- 后续可在 quotes.py 增加 source 字段区分数据来源",
                            "concepts": "- DATA_FLAT：文件在更新，但数值不变（可能市场没动，也可能数据源不刷新）。",
                        },
                    )
                    last_alert_ts = event["ts_utc"]
                    record_emit(key, alert_state, alert_state_path, now_epoch=now_epoch)

            # MOVE
            if prev > 0:
                move = (now - prev) / prev * 100.0
                if debug_enabled:
                    utc_s, _, _ = now_stamps()
                    will_move = abs(move) >= minute_thr
                    print(
                        f"[{utc_s}] DEBUG {sym} prev={prev:.6f} now={now:.6f} "
                        f"move={move:+.2f}% thr={minute_thr:.2f}% flat_count={st.run_len} will_move={will_move}"
                    )
                if abs(move) >= minute_thr:
                    now_epoch = time.time()
                    key = alert_key("MOVE", sym)
                    if not is_on_cooldown(key, cooldown_seconds, alert_state, now_epoch=now_epoch):
                        utc_s, local_s, tzname = now_stamps()
                        msg = (
                            f"[{utc_s} | {local_s} {tzname}] 🚨 MOVE symbol={sym} "
                            f"move={move:+.2f}% prev={prev:.6f} now={now:.6f} now_ts={now_ts.isoformat(timespec='seconds')}"
                        )
                        event = make_event(
                            "MOVE",
                            sym,
                            "high",
                            msg,
                            metrics={
                                "prev": prev,
                                "now": now,
                                "move_pct": move,
                                "threshold": minute_thr,
                                "now_ts": now_ts.isoformat(timespec="seconds"),
                            },
                            source="quotes.csv",
                        )
                        emit_event(
                            event,
                            msg,
                            alerts_log=alerts_log,
                            events_log=events_log,
                            learning_card={
                                "path": learning_cards_path,
                                "alert_type": "MOVE",
                                "symbol": sym,
                                "facts": f"- move={move:+.2f}% (thr={minute_thr:.2f}%)\n- prev={prev:.6f} now={now:.6f}\n- now_ts={now_ts.isoformat(timespec='seconds')}",
                                "hypotheses": "- 市场真实波动\n- 盘后流动性导致跳价\n- 新闻/财报/宏观事件",
                                "checks": "- 同期 SPY 是否同向？\n- 查该标的新闻/公告\n- 查是否财报/分红/拆股相关日期",
                                "concepts": "- MOVE：相邻两条记录的涨跌幅；采样频率由 poll_seconds 决定。",
                            },
                        )
                        last_alert_ts = event["ts_utc"]
                        record_emit(key, alert_state, alert_state_path, now_epoch=now_epoch)

            elif debug_enabled:
                utc_s, _, _ = now_stamps()
                print(
                    f"[{utc_s}] DEBUG {sym} prev={prev:.6f} now={now:.6f} move=0.00% "
                    f"thr={minute_thr:.2f}% flat_count={st.run_len} will_move=False"
                )

        flush_status(quotes_file_age_s, last_rows, last_prices)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()

