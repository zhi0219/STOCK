from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
LOGS_DIR = ROOT / "Logs"
RUNS_ROOT = LOGS_DIR / "train_runs"
LATEST_DIR = RUNS_ROOT / "_latest"
LATEST_REPLAY_INDEX = LATEST_DIR / "replay_index_latest.json"
ARTIFACTS_DIR = ROOT / "artifacts"

SCHEMA_VERSION = 1
DEFAULT_WINDOW = 50
MIN_PRICE_POINTS = 30

REGIME_LABELS = ("TREND", "RANGE", "HIGH_VOL", "LOW_VOL", "INSUFFICIENT_DATA")


@dataclass(frozen=True)
class RegimeMetrics:
    volatility: float | None
    trend_strength: float | None
    volatility_rank: float | None
    trend_rank: float | None
    window_size: int
    returns_count: int
    price_count: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _safe_read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    except Exception:
        return []
    return rows


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _relpath(path: Path | None) -> str | None:
    if path is None:
        return None
    return to_repo_relative(path)


def _find_latest_replay_index() -> tuple[Path | None, dict[str, str]]:
    if LATEST_REPLAY_INDEX.exists():
        return LATEST_REPLAY_INDEX, {"mode": "latest_pointer", "path": _relpath(LATEST_REPLAY_INDEX) or ""}

    candidates = []
    if RUNS_ROOT.exists():
        for run_dir in RUNS_ROOT.iterdir():
            if not run_dir.is_dir() or run_dir.name.startswith("_"):
                continue
            replay_index = run_dir / "replay" / "replay_index.json"
            if replay_index.exists():
                candidates.append(replay_index)
    if not candidates:
        return None, {"mode": "missing", "path": "Logs/train_runs/*/replay/replay_index.json"}
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest, {"mode": "scan", "path": _relpath(latest) or ""}


def _resolve_run_dir(replay_index_path: Path | None, run_dir: Path | None) -> Path | None:
    if run_dir is not None:
        return run_dir
    if replay_index_path is None:
        return None
    if replay_index_path.parent.name == "replay":
        return replay_index_path.parent.parent
    if replay_index_path.parent.name == "_latest":
        return replay_index_path.parent.parent
    return replay_index_path.parent


def _load_decision_cards(
    replay_index_payload: dict[str, Any], replay_index_path: Path | None
) -> tuple[list[dict[str, Any]], Path | None]:
    pointers = replay_index_payload.get("pointers") if isinstance(replay_index_payload.get("pointers"), dict) else {}
    decision_rel = pointers.get("decision_cards") if isinstance(pointers, dict) else None
    decision_path = None
    if isinstance(decision_rel, str) and decision_rel:
        decision_path = ROOT / decision_rel
    elif replay_index_path is not None:
        candidate = replay_index_path.parent / "decision_cards.jsonl"
        if candidate.exists():
            decision_path = candidate
    if decision_path is None or not decision_path.exists():
        return [], decision_path
    return _safe_read_jsonl(decision_path), decision_path


def _extract_prices(cards: list[dict[str, Any]]) -> list[tuple[datetime | None, float]]:
    prices: list[tuple[datetime | None, float]] = []
    for card in cards:
        snapshot = card.get("price_snapshot") if isinstance(card.get("price_snapshot"), dict) else {}
        price = snapshot.get("last")
        if isinstance(price, (int, float)):
            prices.append((_parse_ts(card.get("ts_utc")), float(price)))
    if any(ts for ts, _ in prices):
        prices.sort(key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))
    return prices


def _returns_from_prices(prices: list[tuple[datetime | None, float]]) -> list[float]:
    returns: list[float] = []
    values = [price for _, price in prices if isinstance(price, (int, float))]
    for prev, curr in zip(values, values[1:]):
        if prev == 0:
            continue
        returns.append((curr - prev) / prev)
    return returns


def _std(values: list[float]) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5


def _rolling_metrics(returns: list[float], window: int) -> list[dict[str, float]]:
    metrics: list[dict[str, float]] = []
    if window <= 0 or len(returns) < window:
        return metrics
    for idx in range(window, len(returns) + 1):
        chunk = returns[idx - window : idx]
        vol = _std(chunk)
        if vol is None:
            continue
        total_return = sum(chunk)
        trend_strength = abs(total_return) / (vol * (len(chunk) ** 0.5)) if vol > 0 else 0.0
        metrics.append({"volatility": vol, "trend_strength": trend_strength})
    return metrics


def _percentile_rank(value: float | None, values: Iterable[float]) -> float | None:
    values_list = [v for v in values if isinstance(v, (int, float))]
    if value is None or not values_list:
        return None
    count = sum(1 for v in values_list if v <= value)
    return count / len(values_list)


def _label_regime(metrics: RegimeMetrics) -> str:
    if metrics.price_count < MIN_PRICE_POINTS or metrics.returns_count <= 0:
        return "INSUFFICIENT_DATA"
    vol_rank = metrics.volatility_rank
    trend_rank = metrics.trend_rank
    if vol_rank is not None and vol_rank >= 0.75:
        return "HIGH_VOL"
    if vol_rank is not None and vol_rank <= 0.25:
        return "LOW_VOL"
    if trend_rank is not None and trend_rank >= 0.6:
        return "TREND"
    return "RANGE"


def classify_prices(prices: list[tuple[datetime | None, float]], window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    price_count = len(prices)
    returns = _returns_from_prices(prices)
    window = max(5, int(window))
    window_metrics = _rolling_metrics(returns, window)
    latest_metrics = window_metrics[-1] if window_metrics else {}
    volatility = latest_metrics.get("volatility") if latest_metrics else None
    trend_strength = latest_metrics.get("trend_strength") if latest_metrics else None
    volatility_rank = _percentile_rank(volatility, [m.get("volatility") for m in window_metrics])
    trend_rank = _percentile_rank(trend_strength, [m.get("trend_strength") for m in window_metrics])

    metrics = RegimeMetrics(
        volatility=volatility,
        trend_strength=trend_strength,
        volatility_rank=volatility_rank,
        trend_rank=trend_rank,
        window_size=window,
        returns_count=len(returns),
        price_count=price_count,
    )
    label = _label_regime(metrics)
    missing_reasons = []
    if price_count < MIN_PRICE_POINTS:
        missing_reasons.append("insufficient_price_points")
    if len(returns) < window:
        missing_reasons.append("insufficient_window_returns")

    return {
        "label": label,
        "status": "PASS" if label != "INSUFFICIENT_DATA" else "INSUFFICIENT_DATA",
        "metrics": {
            "volatility": metrics.volatility,
            "trend_strength": metrics.trend_strength,
            "volatility_rank": metrics.volatility_rank,
            "trend_rank": metrics.trend_rank,
            "window_size": metrics.window_size,
            "returns_count": metrics.returns_count,
            "price_count": metrics.price_count,
        },
        "missing_reasons": missing_reasons,
        "window_count": len(window_metrics),
        "window_metrics": window_metrics,
    }


def build_report(
    *,
    replay_index_path: Path | None = None,
    run_dir: Path | None = None,
    window: int = DEFAULT_WINDOW,
) -> dict[str, Any]:
    created_utc = _now_iso()
    missing_reasons: list[str] = []

    source = {"mode": "unknown", "path": ""}
    if replay_index_path is None and run_dir is None:
        replay_index_path, source = _find_latest_replay_index()
    if run_dir is not None and replay_index_path is None:
        source = {"mode": "run_dir", "path": _relpath(run_dir) or ""}

    replay_index_payload = _safe_read_json(replay_index_path) if replay_index_path else None
    if replay_index_path and not replay_index_payload:
        missing_reasons.append("replay_index_unreadable")

    if replay_index_path is None and run_dir is None:
        missing_reasons.append("replay_index_missing")

    resolved_run_dir = _resolve_run_dir(replay_index_path, run_dir)

    decision_cards, decision_path = ([], None)
    if replay_index_payload:
        decision_cards, decision_path = _load_decision_cards(replay_index_payload, replay_index_path)

    if not decision_cards:
        missing_reasons.append("decision_cards_missing")

    prices = _extract_prices(decision_cards)
    classification = classify_prices(prices, window=window)

    report = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": created_utc,
        "run_id": replay_index_payload.get("run_id") if replay_index_payload else (resolved_run_dir.name if resolved_run_dir else None),
        "label": classification.get("label"),
        "status": classification.get("status"),
        "window_size": classification.get("metrics", {}).get("window_size"),
        "metrics": classification.get("metrics"),
        "missing_reasons": sorted(set(missing_reasons + classification.get("missing_reasons", []))),
        "window_count": classification.get("window_count"),
        "window_metrics": classification.get("window_metrics"),
        "source": source,
        "evidence": {
            "replay_index": _relpath(replay_index_path),
            "decision_cards": _relpath(decision_path),
        },
    }
    return report


def write_regime_report(
    report: dict[str, Any],
    run_dir: Path | None,
    artifacts_output: Path | None,
    windows_output: Path | None,
) -> dict[str, Path]:
    outputs: dict[str, Path] = {}
    if run_dir is not None:
        report_path = run_dir / "regime_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        outputs["run_report"] = report_path
        latest_dir = run_dir / "_latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        latest_path = latest_dir / "regime_report_latest.json"
        latest_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        outputs["latest"] = latest_path

    if artifacts_output is not None:
        artifacts_output.parent.mkdir(parents=True, exist_ok=True)
        artifacts_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        outputs["artifacts"] = artifacts_output

    if windows_output is not None:
        windows_output.parent.mkdir(parents=True, exist_ok=True)
        windows_payload = classification_windows(report)
        windows_output.write_text(windows_payload, encoding="utf-8")
        outputs["windows"] = windows_output
    return outputs


def classification_windows(report: dict[str, Any]) -> str:
    windows = report.get("window_metrics", []) if isinstance(report.get("window_metrics"), list) else []
    return "\n".join(json.dumps(entry, ensure_ascii=False) for entry in windows) + ("\n" if windows else "")


def _should_write_artifacts(default: Path | None) -> Path | None:
    if default is not None:
        return default
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return ARTIFACTS_DIR / "regime_report.json"
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regime classifier (SIM-only, read-only)")
    parser.add_argument("--replay-index", help="Path to replay_index.json or replay_index_latest.json")
    parser.add_argument("--run-dir", help="Run directory containing replay artifacts")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="Rolling window length")
    parser.add_argument("--artifacts-output", help="Optional artifacts output path")
    parser.add_argument("--windows-output", help="Optional per-window JSONL output path")
    parser.add_argument("--no-artifacts-output", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or __import__("sys").argv[1:])
    replay_index_path = Path(args.replay_index) if args.replay_index else None
    run_dir = Path(args.run_dir) if args.run_dir else None
    if replay_index_path and not replay_index_path.is_absolute():
        replay_index_path = (ROOT / replay_index_path).resolve()
    if run_dir and not run_dir.is_absolute():
        run_dir = (ROOT / run_dir).resolve()

    artifacts_output = None
    if not args.no_artifacts_output:
        artifacts_output = (
            Path(args.artifacts_output)
            if args.artifacts_output
            else _should_write_artifacts(None)
        )
        if artifacts_output and not artifacts_output.is_absolute():
            artifacts_output = (ROOT / artifacts_output).resolve()

    windows_output = Path(args.windows_output) if args.windows_output else None
    if windows_output and not windows_output.is_absolute():
        windows_output = (ROOT / windows_output).resolve()

    report = build_report(replay_index_path=replay_index_path, run_dir=run_dir, window=args.window)
    outputs = write_regime_report(report, run_dir, artifacts_output, windows_output)
    status = report.get("status")
    label = report.get("label")
    print(f"REGIME_CLASSIFIER_SUMMARY|status={status}|label={label}|window={args.window}")
    if outputs:
        print(f"report_paths={','.join(_relpath(path) or '' for path in outputs.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_report", "classify_prices", "write_regime_report"]
