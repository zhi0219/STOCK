from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Dict

from tools.paths import repo_root

DEFAULT_POLICY = {
    "schema_version": 2,
    "fee_per_trade": 0.5,
    "fee_per_share": 0.001,
    "spread_bps": 5.0,
    "slippage_bps": 8.0,
    "latency_ms": 750,
    "partial_fill_prob": 0.12,
    "max_fill_fraction": 0.7,
    "reject_prob": 0.02,
    "fail_prob": 0.01,
    "gap_bps": 12.0,
    "gap_threshold_pct": 0.5,
}


def _coerce_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def _stable_seed(*payloads: object) -> int:
    encoded = json.dumps(payloads, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _pick_prev_price(snapshot: Dict[str, object]) -> float:
    for key in ("prev_price", "prev_close", "prior_price", "price_prev"):
        if key in snapshot:
            return _coerce_float(snapshot.get(key), 0.0)
    return 0.0


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
        "schema_version": int(merged.get("schema_version", DEFAULT_POLICY["schema_version"])),
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
        "reject_prob": _coerce_float(merged.get("reject_prob"), DEFAULT_POLICY["reject_prob"]),
        "fail_prob": _coerce_float(merged.get("fail_prob"), DEFAULT_POLICY["fail_prob"]),
        "gap_bps": _coerce_float(merged.get("gap_bps"), DEFAULT_POLICY["gap_bps"]),
        "gap_threshold_pct": _coerce_float(
            merged.get("gap_threshold_pct"), DEFAULT_POLICY["gap_threshold_pct"]
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
    gap_bps = 0.0
    gap_pct = 0.0
    prev_price = _pick_prev_price(market_snapshot)
    if prev_price > 0:
        gap_pct = abs(price - prev_price) / prev_price * 100.0
        if gap_pct >= _coerce_float(policy.get("gap_threshold_pct"), 0.0):
            gap_bps = _coerce_float(policy.get("gap_bps"), 0.0)

    total_bps = (spread_bps + slippage_bps + gap_bps) / 10_000.0

    if side == "SELL" or qty < 0:
        fill_price = price * (1.0 - total_bps)
    else:
        fill_price = price * (1.0 + total_bps)

    fill_fraction = 1.0
    partial_fill = False
    seed_used: int | None = None
    rng = None
    if rng_seed is not None:
        seed_used = int(rng_seed)
    else:
        seed_used = _stable_seed(order, market_snapshot, policy)
    rng = random.Random(seed_used)
    partial_prob = _coerce_float(policy.get("partial_fill_prob"), 0.0)
    max_fraction = max(0.0, min(1.0, _coerce_float(policy.get("max_fill_fraction"), 1.0)))
    rejection_prob = max(0.0, min(1.0, _coerce_float(policy.get("reject_prob"), 0.0)))
    failure_prob = max(0.0, min(1.0, _coerce_float(policy.get("fail_prob"), 0.0)))
    fill_status = "FILLED"
    reject_reason: str | None = None
    if rng is not None:
        if rng.random() < failure_prob:
            fill_status = "FAILED"
            reject_reason = "execution_failure"
        elif rng.random() < rejection_prob:
            fill_status = "REJECTED"
            reject_reason = "order_rejected"
    if fill_status == "FILLED" and partial_prob > 0.0 and max_fraction < 1.0 and rng is not None:
        if rng.random() < partial_prob:
            fill_fraction = max_fraction
            partial_fill = True

    fill_qty = qty * fill_fraction
    fee_per_trade = _coerce_float(policy.get("fee_per_trade"), 0.0)
    fee_per_share = _coerce_float(policy.get("fee_per_share"), 0.0)
    fee_usd = fee_per_trade + fee_per_share * abs(fill_qty)
    if fill_status != "FILLED":
        fill_qty = 0.0
        fee_usd = fee_per_trade
        fill_fraction = 0.0
        partial_fill = False
    latency_sec = _coerce_float(policy.get("latency_ms"), 0.0) / 1000.0

    return {
        "fill_qty": fill_qty,
        "fill_price": fill_price,
        "fee_usd": fee_usd,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "gap_bps": gap_bps,
        "gap_pct": gap_pct,
        "latency_sec": latency_sec,
        "fill_fraction": fill_fraction,
        "partial_fill": partial_fill,
        "rng_seed": seed_used,
        "fill_status": fill_status,
        "reject_reason": reject_reason,
        "reject_prob": rejection_prob,
        "fail_prob": failure_prob,
    }


__all__ = ["apply_friction", "load_friction_policy"]
