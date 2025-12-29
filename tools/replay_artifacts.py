from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.fs_atomic import atomic_write_json, atomic_write_text
from tools.paths import repo_root, to_repo_relative

REPLAY_SCHEMA_VERSION = 1
DECISION_CARD_SCHEMA_VERSION = 1
MAX_DECISION_CARDS = 2000
MAX_DECISION_CARDS_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ReplayTruncation:
    truncated: bool
    max_cards: int
    max_bytes: int
    dropped_cards: int


@dataclass(frozen=True)
class ReplayOutputs:
    run_dir: Path
    replay_index: Path
    decision_cards: Path
    replay_events: Path
    replay_index_latest: Path
    decision_cards_latest: Path
    truncation: ReplayTruncation
    num_cards: int
    num_events: int


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _price_snapshot(last_price: float | None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"last": last_price}
    if last_price is not None:
        snapshot["currency"] = "USD"
    return snapshot


def _runner_info() -> dict[str, Any]:
    return {
        "python": Path(sys.executable).name,
        "platform": platform.platform(),
    }


def _signals_from_metrics(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"name": "score", "value": metrics.get("score")},
        {"name": "pnl_proxy", "value": metrics.get("pnl_proxy"), "unit": "usd"},
        {"name": "max_drawdown_pct", "value": metrics.get("max_drawdown_pct"), "unit": "pct"},
        {"name": "reject_rate", "value": metrics.get("reject_rate"), "unit": "ratio"},
        {"name": "turnover", "value": metrics.get("turnover")},
        {"name": "steps", "value": metrics.get("steps")},
    ]


def _evidence_paths(paths: Iterable[Path]) -> list[str]:
    root = repo_root()
    rels: list[str] = []
    for path in paths:
        if not path:
            continue
        resolved = path
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if str(resolved).startswith(str(root.resolve())):
            rels.append(to_repo_relative(resolved))
    return rels


def _build_entry_card(
    entry: dict[str, Any],
    step_id: int,
    run_id: str,
    ts_utc: str,
    last_price: float | None,
    data_health: str,
    evidence: list[str],
) -> dict[str, Any]:
    metrics = entry.get("metrics", {}) if isinstance(entry.get("metrics"), dict) else {}
    safety_pass = bool(entry.get("safety_pass", True))
    safety_failures = entry.get("safety_failures", [])
    reject_codes = safety_failures if isinstance(safety_failures, list) else []
    if not safety_pass and not reject_codes:
        reject_codes = ["safety_reject"]

    action = "HOLD" if safety_pass else "REJECT"
    return {
        "schema_version": DECISION_CARD_SCHEMA_VERSION,
        "ts_utc": ts_utc,
        "step_id": step_id,
        "episode_id": run_id,
        "symbol": str(entry.get("candidate_id") or "n/a"),
        "action": action,
        "size": 0,
        "price_snapshot": _price_snapshot(last_price),
        "signals": _signals_from_metrics(metrics),
        "guards": {
            "kill_switch": False,
            "data_health": data_health,
            "cooldown_ok": True,
            "limits_ok": safety_pass,
            "no_lookahead_ok": True,
            "walk_forward_window_id": None,
        },
        "decision": {
            "accepted": safety_pass,
            "reject_reason_codes": reject_codes,
        },
        "evidence": {"paths": evidence},
        "pnl_delta": metrics.get("pnl_proxy"),
        "equity": metrics.get("final_equity_usd"),
        "drawdown": metrics.get("max_drawdown_pct"),
    }


def _build_judge_card(
    judge_payload: dict[str, Any],
    step_id: str,
    run_id: str,
    ts_utc: str,
    last_price: float | None,
    data_health: str,
    evidence: list[str],
) -> dict[str, Any]:
    status = str(judge_payload.get("status") or "unknown")
    accepted = status == "PASS"
    reasons = judge_payload.get("reasons", []) if isinstance(judge_payload.get("reasons"), list) else []
    scores = judge_payload.get("scores", {}) if isinstance(judge_payload.get("scores"), dict) else {}
    thresholds = judge_payload.get("thresholds", {}) if isinstance(judge_payload.get("thresholds"), dict) else {}
    action = "HOLD" if accepted else "REJECT"
    signals = [
        {"name": "judge_status", "value": status},
        {"name": "candidate_score", "value": scores.get("candidate")},
        {"name": "min_advantage", "value": thresholds.get("min_advantage")},
        {"name": "insufficient_data", "value": judge_payload.get("insufficient_data")},
    ]
    return {
        "schema_version": DECISION_CARD_SCHEMA_VERSION,
        "ts_utc": ts_utc,
        "step_id": step_id,
        "episode_id": run_id,
        "symbol": "n/a",
        "action": action,
        "size": 0,
        "price_snapshot": _price_snapshot(last_price),
        "signals": signals,
        "guards": {
            "kill_switch": False,
            "data_health": data_health,
            "cooldown_ok": True,
            "limits_ok": accepted,
            "no_lookahead_ok": True,
            "walk_forward_window_id": None,
        },
        "decision": {
            "accepted": accepted,
            "reject_reason_codes": reasons,
        },
        "evidence": {"paths": evidence},
    }


def _build_promotion_card(
    promotion_payload: dict[str, Any],
    step_id: str,
    run_id: str,
    ts_utc: str,
    last_price: float | None,
    data_health: str,
    evidence: list[str],
) -> dict[str, Any]:
    decision = str(promotion_payload.get("decision") or "REJECT")
    accepted = decision == "APPROVE"
    reasons = promotion_payload.get("reasons", []) if isinstance(promotion_payload.get("reasons"), list) else []
    action = "NOOP" if accepted else "REJECT"
    signals = [
        {"name": "promotion_decision", "value": decision},
        {"name": "promoted", "value": promotion_payload.get("promoted")},
        {"name": "candidate_id", "value": promotion_payload.get("candidate_id")},
    ]
    return {
        "schema_version": DECISION_CARD_SCHEMA_VERSION,
        "ts_utc": ts_utc,
        "step_id": step_id,
        "episode_id": run_id,
        "symbol": "n/a",
        "action": action,
        "size": 0,
        "price_snapshot": _price_snapshot(last_price),
        "signals": signals,
        "guards": {
            "kill_switch": False,
            "data_health": data_health,
            "cooldown_ok": True,
            "limits_ok": accepted,
            "no_lookahead_ok": True,
            "walk_forward_window_id": None,
        },
        "decision": {
            "accepted": accepted,
            "reject_reason_codes": reasons,
        },
        "evidence": {"paths": evidence},
    }


def build_decision_cards(
    tournament_payload: dict[str, Any],
    judge_payload: dict[str, Any],
    promotion_payload: dict[str, Any],
    run_id: str,
    ts_utc: str,
    last_price: float | None,
    data_health: str,
    evidence_paths: dict[str, Path],
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    entries = tournament_payload.get("entries", []) if isinstance(tournament_payload.get("entries"), list) else []
    entry_evidence = _evidence_paths(
        [
            evidence_paths.get("tournament_result"),
            evidence_paths.get("promotion_history"),
        ]
    )
    for idx, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        cards.append(
            _build_entry_card(
                entry=entry,
                step_id=idx,
                run_id=run_id,
                ts_utc=ts_utc,
                last_price=last_price,
                data_health=data_health,
                evidence=entry_evidence,
            )
        )

    judge_evidence = _evidence_paths([evidence_paths.get("judge_result")])
    cards.append(
        _build_judge_card(
            judge_payload=judge_payload,
            step_id="judge",
            run_id=run_id,
            ts_utc=ts_utc,
            last_price=last_price,
            data_health=data_health,
            evidence=judge_evidence,
        )
    )

    promotion_evidence = _evidence_paths([evidence_paths.get("promotion_decision")])
    cards.append(
        _build_promotion_card(
            promotion_payload=promotion_payload,
            step_id="promotion",
            run_id=run_id,
            ts_utc=ts_utc,
            last_price=last_price,
            data_health=data_health,
            evidence=promotion_evidence,
        )
    )
    return cards


def _apply_bounds(cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], ReplayTruncation]:
    truncated = False
    dropped_cards = 0

    if len(cards) > MAX_DECISION_CARDS:
        dropped_cards += len(cards) - MAX_DECISION_CARDS
        cards = cards[-MAX_DECISION_CARDS:]
        truncated = True

    lines = [json.dumps(card, ensure_ascii=False) for card in cards]
    encoded_lines = [line.encode("utf-8") for line in lines]
    total_bytes = sum(len(line) + 1 for line in encoded_lines)
    if total_bytes > MAX_DECISION_CARDS_BYTES:
        truncated = True
        kept_lines: list[str] = []
        kept_cards: list[dict[str, Any]] = []
        running = 0
        for line, card in zip(reversed(lines), reversed(cards)):
            line_bytes = len(line.encode("utf-8")) + 1
            if running + line_bytes > MAX_DECISION_CARDS_BYTES:
                continue
            kept_lines.append(line)
            kept_cards.append(card)
            running += line_bytes
        kept_lines.reverse()
        kept_cards.reverse()
        dropped_cards += len(cards) - len(kept_cards)
        cards = kept_cards

    truncation = ReplayTruncation(
        truncated=truncated,
        max_cards=MAX_DECISION_CARDS,
        max_bytes=MAX_DECISION_CARDS_BYTES,
        dropped_cards=dropped_cards,
    )
    return cards, truncation


def _cards_to_jsonl(cards: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(card, ensure_ascii=False) for card in cards) + ("\n" if cards else "")


def _events_from_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for card in cards:
        events.append(
            {
                "ts_utc": card.get("ts_utc"),
                "event_type": "REPLAY_DECISION_CARD",
                "step_id": card.get("step_id"),
                "episode_id": card.get("episode_id"),
                "action": card.get("action"),
                "accepted": card.get("decision", {}).get("accepted"),
            }
        )
    return events


def write_replay_artifacts(
    run_dir: Path,
    run_id: str,
    git_commit: str | None,
    decision_cards: list[dict[str, Any]],
    replay_events: list[dict[str, Any]] | None = None,
) -> ReplayOutputs:
    replay_dir = run_dir / "replay"
    latest_dir = run_dir / "_latest"
    replay_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)

    decision_cards, truncation = _apply_bounds(decision_cards)
    replay_events = replay_events if replay_events is not None else _events_from_cards(decision_cards)

    decision_cards_path = replay_dir / "decision_cards.jsonl"
    replay_events_path = replay_dir / "replay_events.jsonl"
    replay_index_path = replay_dir / "replay_index.json"
    decision_cards_latest_path = latest_dir / "decision_cards_latest.jsonl"
    replay_index_latest_path = latest_dir / "replay_index_latest.json"

    decision_payload = _cards_to_jsonl(decision_cards)
    events_payload = "\n".join(json.dumps(event, ensure_ascii=False) for event in replay_events)
    if events_payload:
        events_payload += "\n"

    atomic_write_text(decision_cards_path, decision_payload)
    atomic_write_text(decision_cards_latest_path, decision_payload)
    atomic_write_text(replay_events_path, events_payload)

    index_payload = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "created_ts_utc": _now_ts(),
        "run_id": run_id,
        "git_commit": git_commit or "unknown",
        "runner": _runner_info(),
        "counts": {
            "num_cards": len(decision_cards),
            "num_events": len(replay_events),
        },
        "truncation": {
            "truncated": truncation.truncated,
            "max_cards": truncation.max_cards,
            "max_bytes": truncation.max_bytes,
            "dropped_cards": truncation.dropped_cards,
        },
        "pointers": {
            "decision_cards": to_repo_relative(decision_cards_path),
            "replay_events": to_repo_relative(replay_events_path),
        },
    }
    atomic_write_json(replay_index_path, index_payload)
    atomic_write_json(replay_index_latest_path, index_payload)

    return ReplayOutputs(
        run_dir=run_dir,
        replay_index=replay_index_path,
        decision_cards=decision_cards_path,
        replay_events=replay_events_path,
        replay_index_latest=replay_index_latest_path,
        decision_cards_latest=decision_cards_latest_path,
        truncation=truncation,
        num_cards=len(decision_cards),
        num_events=len(replay_events),
    )
