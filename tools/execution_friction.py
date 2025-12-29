from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict

from tools.paths import repo_root

DEFAULT_POLICY = {
    "schema_version": 1,
    "fee_per_trade": 0.25,
    "fee_per_share": 0.0,
    "spread_bps": 1.0,
    "slippage_bps": 3.0,
    "latency_ms": 400,
    "partial_fill_prob": 0.0,
    "max_fill_fraction": 1.0,
}


def _coerce_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def load_friction_policy(path: Path | None = None) -> Dict[str, float | int]:
    policy_path = path or (repo_root() / "Data" / "friction_policy.json")
    if not policy_path.is_absolute():
        policy_path = repo_root() / policy_path
    policy_path = policy_path.expanduser().resolve()
    payload: Dict[str, object] = {}
    if policy_path.exists():
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    merged = dict(DEFAULT_POLICY)
    if isinstance(payload, dict):
        merged.update(payload)
    return {
        "schema_version": int(merged.get("schema_version", 1)),
        "fee_per_trade": _coerce_float(merged.get("fee_per_trade"), DEFAULT_POLICY["fee_per_trade"]),
        "fee_per_share": _coerce_float(merged.get("fee_per_share"), DEFAULT_POLICY["fee_per_share"]),
        "spread_bps": _coerce_float(merged.get("spread_bps"), DEFAULT_POLICY["spread_bps"]),
        "slippage_bps": _coerce_float(merged.get("slippage_bps"), DEFAULT_POLICY["slippage_bps"]),
        "latency_ms": _coerce_float(merged.get("latency_ms"), DEFAULT_POLICY["latency_ms"]),
        "partial_fill_prob": _coerce_float(
            merged.get("partial_fill_prob"), DEFAULT_POLICY["partial_fill_prob"]
        ),
        "max_fill_fraction": _coerce_float(
            merged.get("max_fill_fraction"), DEFAULT_POLICY["max_fill_fraction"]
        ),
    }


def apply_friction(
    order: Dict[str, object],
    market_snapshot: Dict[str, object],
    policy: Dict[str, float | int],
    rng_seed: int | None = None,
) -> Dict[str, object]:
    qty = _coerce_float(order.get("qty"), 0.0)
    price = _coerce_float(order.get("price"), _coerce_float(market_snapshot.get("price"), 0.0))
    side = str(order.get("side") or "").upper()
    if not side:
        side = "BUY" if qty >= 0 else "SELL"

    spread_bps = _coerce_float(policy.get("spread_bps"), 0.0)
    slippage_bps = _coerce_float(policy.get("slippage_bps"), 0.0)
    total_bps = (spread_bps + slippage_bps) / 10_000.0

    if side == "SELL" or qty < 0:
        fill_price = price * (1.0 - total_bps)
    else:
        fill_price = price * (1.0 + total_bps)

    fill_fraction = 1.0
    partial_fill = False
    seed_used = None
    partial_prob = _coerce_float(policy.get("partial_fill_prob"), 0.0)
    max_fraction = max(0.0, min(1.0, _coerce_float(policy.get("max_fill_fraction"), 1.0)))
    if rng_seed is not None and partial_prob > 0.0 and max_fraction < 1.0:
        rng = random.Random(int(rng_seed))
        if rng.random() < partial_prob:
            fill_fraction = max_fraction
            partial_fill = True
            seed_used = int(rng_seed)

    fill_qty = qty * fill_fraction
    fee_per_trade = _coerce_float(policy.get("fee_per_trade"), 0.0)
    fee_per_share = _coerce_float(policy.get("fee_per_share"), 0.0)
    fee_usd = fee_per_trade + fee_per_share * abs(fill_qty)
    latency_sec = _coerce_float(policy.get("latency_ms"), 0.0) / 1000.0

    return {
        "fill_qty": fill_qty,
        "fill_price": fill_price,
        "fee_usd": fee_usd,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "latency_sec": latency_sec,
        "fill_fraction": fill_fraction,
        "partial_fill": partial_fill,
        "rng_seed": seed_used,
    }


__all__ = ["apply_friction", "load_friction_policy"]
