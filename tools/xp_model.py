from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Iterable

from tools.paths import repo_root, to_repo_relative

XP_SPEC_VERSION = "v1"
SCHEMA_VERSION = 1
LEVEL_THRESHOLDS = [0, 100, 250, 450, 700, 1000, 1400, 1850, 2350]
ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _safe_relpath(path: Path | None) -> str | None:
    if path is None:
        return None
    rel = to_repo_relative(path)
    if not rel:
        return None
    if rel.startswith("/") or ABS_PATH_PATTERN.search(rel):
        return None
    if re.match(r"^[A-Za-z]:", rel):
        return None
    return rel.replace("\\", "/")


def _collect_evidence(paths: Iterable[Path | None]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for path in paths:
        rel = _safe_relpath(path)
        if not rel or rel in seen:
            continue
        seen.add(rel)
        items.append(rel)
    return items


def _level_from_xp(xp_total: int) -> tuple[int, float]:
    xp_total = max(0, int(xp_total))
    level = 1
    for idx, threshold in enumerate(LEVEL_THRESHOLDS, start=1):
        if xp_total >= threshold:
            level = idx
    if level >= len(LEVEL_THRESHOLDS):
        return level, 1.0
    prev_threshold = LEVEL_THRESHOLDS[level - 1]
    next_threshold = LEVEL_THRESHOLDS[level]
    span = max(1, next_threshold - prev_threshold)
    progress = (xp_total - prev_threshold) / span
    return level, max(0.0, min(1.0, round(progress, 4)))


def compute_xp_snapshot(
    *,
    tournament: dict[str, Any] | None,
    judge: dict[str, Any] | None,
    promotion: dict[str, Any] | None,
    promotion_history: dict[str, Any] | None,
    promotion_history_events: list[dict[str, Any]] | None,
    walk_forward_result: dict[str, Any] | None,
    no_lookahead_audit: dict[str, Any] | None,
    doctor_report: dict[str, Any] | None,
    repo_hygiene: dict[str, Any] | None,
    evidence_paths: dict[str, Path | None],
    created_utc: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    created_utc = created_utc or _now_iso()
    breakdown: list[dict[str, Any]] = []
    missing_reasons: list[str] = []
    xp_total = 0

    def add_item(
        *,
        key: str,
        label: str,
        value: Any,
        points: int,
        evidence: Iterable[Path | None] = (),
        notes: str | None = None,
    ) -> None:
        nonlocal xp_total
        entry: dict[str, Any] = {
            "key": key,
            "label": label,
            "value": value,
            "points": int(points),
            "evidence_paths_rel": _collect_evidence(evidence),
        }
        if notes:
            entry["notes"] = notes
        breakdown.append(entry)
        xp_total += int(points)

    def add_insufficient(reason: str, penalty: int, evidence: Iterable[Path | None]) -> None:
        missing_reasons.append(reason)
        add_item(
            key=f"insufficient_{len(missing_reasons)}",
            label="INSUFFICIENT_DATA",
            value=reason,
            points=penalty,
            evidence=evidence,
            notes="Missing required artifact or fields.",
        )

    judge_scores = judge.get("scores") if isinstance(judge, dict) else {}
    advantages = {}
    if isinstance(judge_scores, dict):
        advantages = (
            judge_scores.get("advantages")
            if isinstance(judge_scores.get("advantages"), dict)
            else {}
        )
    baseline_labels = {
        "baseline_do_nothing": "DoNothing",
        "baseline_buy_hold": "Buy&Hold",
    }
    if advantages:
        for baseline_id, label in baseline_labels.items():
            if baseline_id not in advantages:
                add_insufficient(f"missing_advantage:{baseline_id}", -6, [evidence_paths.get("judge")])
                continue
            delta = float(advantages.get(baseline_id) or 0.0)
            points = _clamp(int(round(delta * 100)), -40, 60)
            add_item(
                key=f"advantage_{baseline_id}",
                label=f"Advantage vs {label}",
                value=round(delta, 4),
                points=points,
                evidence=[evidence_paths.get("judge")],
                notes="Derived from PR28 judge_result scores. Positive deltas earn points.",
            )
    else:
        add_insufficient("missing_judge_advantages", -12, [evidence_paths.get("judge")])

    candidate_id = None
    if isinstance(judge, dict):
        candidate_id = judge.get("candidate_id") or candidate_id
    if isinstance(promotion, dict):
        candidate_id = promotion.get("candidate_id") or candidate_id

    candidate_metrics = None
    if isinstance(tournament, dict) and candidate_id:
        entries = tournament.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("candidate_id") == candidate_id:
                    candidate_metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else None
                    break
    if candidate_metrics and isinstance(candidate_metrics, dict):
        drawdown = candidate_metrics.get("max_drawdown_pct")
        if isinstance(drawdown, (int, float)):
            penalty = -int(round(max(0.0, float(drawdown) - 5.0) * 2.0))
            penalty = _clamp(penalty, -30, 0)
            add_item(
                key="risk_drawdown",
                label="Risk discipline: max drawdown",
                value=round(float(drawdown), 4),
                points=penalty,
                evidence=[evidence_paths.get("tournament")],
                notes="Penalizes drawdown above 5%.",
            )
        else:
            add_insufficient("missing_drawdown_metric", -5, [evidence_paths.get("tournament")])

        volatility = candidate_metrics.get("volatility_proxy")
        if isinstance(volatility, (int, float)):
            penalty = -int(round(max(0.0, float(volatility) - 0.02) * 200.0))
            penalty = _clamp(penalty, -20, 0)
            add_item(
                key="risk_volatility",
                label="Risk discipline: volatility",
                value=round(float(volatility), 6),
                points=penalty,
                evidence=[evidence_paths.get("tournament")],
                notes="Penalizes volatility proxy above 0.02.",
            )
        else:
            add_insufficient("missing_volatility_metric", -5, [evidence_paths.get("tournament")])
    else:
        add_insufficient("missing_candidate_metrics", -10, [evidence_paths.get("tournament")])

    if promotion_history_events and len(promotion_history_events) >= 3:
        recent = promotion_history_events[-5:]
        decisions = [str(event.get("decision")) for event in recent if isinstance(event, dict)]
        unique = {d for d in decisions if d}
        if len(unique) == 1 and decisions:
            points = 10
            value = f"{decisions[-1]} x{len(decisions)}"
        else:
            points = -5
            value = "mixed"
        add_item(
            key="stability_promotion_history",
            label="Stability: promotion history consistency",
            value=value,
            points=points,
            evidence=[evidence_paths.get("promotion_history"), evidence_paths.get("promotion_history_jsonl")],
            notes="Uses recent promotion decision consistency as a proxy for stability.",
        )
    else:
        add_insufficient("stability_history_unavailable", -5, [evidence_paths.get("promotion_history")])

    if walk_forward_result and isinstance(walk_forward_result, dict):
        wf_status = str(walk_forward_result.get("status") or "UNKNOWN")
        wf_passes = int(walk_forward_result.get("window_passes") or 0)
        wf_required = int(walk_forward_result.get("window_passes_required") or 0)
        wf_points = 10 if wf_status == "PASS" and wf_passes >= wf_required else -10
        wf_value = f"{wf_status} {wf_passes}/{wf_required}" if wf_required else wf_status
        add_item(
            key="stability_walk_forward",
            label="Stability: walk-forward windows",
            value=wf_value,
            points=wf_points,
            evidence=[evidence_paths.get("walk_forward"), evidence_paths.get("walk_forward_windows")],
            notes="Uses walk-forward evaluation window pass rate.",
        )
    else:
        add_insufficient("missing_walk_forward_result", -8, [evidence_paths.get("walk_forward")])

    if doctor_report and isinstance(doctor_report, dict):
        kill_switch_present = bool(doctor_report.get("kill_switch_present"))
        kill_points = -15 if kill_switch_present else 5
        add_item(
            key="safety_kill_switch",
            label="Safety: kill switch status",
            value="TRIPPED" if kill_switch_present else "CLEAR",
            points=kill_points,
            evidence=[evidence_paths.get("doctor_report")],
        )

        runtime_write = doctor_report.get("runtime_write_health", {})
        runtime_status = (
            runtime_write.get("status")
            if isinstance(runtime_write, dict)
            else None
        )
        runtime_points = 5 if runtime_status == "PASS" else -10
        add_item(
            key="safety_runtime_write",
            label="Safety: runtime write health",
            value=runtime_status or "UNKNOWN",
            points=runtime_points,
            evidence=[evidence_paths.get("doctor_report")],
        )

        hygiene_status = None
        if isinstance(repo_hygiene, dict):
            hygiene_status = repo_hygiene.get("status")
        if hygiene_status is None:
            hygiene_summary = doctor_report.get("repo_hygiene_summary", {})
            if isinstance(hygiene_summary, dict):
                hygiene_status = hygiene_summary.get("status")
        if hygiene_status is None:
            add_insufficient("missing_repo_hygiene_status", -5, [evidence_paths.get("doctor_report")])
        else:
            hygiene_points = 5 if str(hygiene_status) == "PASS" else -10
            add_item(
                key="safety_repo_hygiene",
                label="Safety: repo hygiene",
                value=str(hygiene_status),
                points=hygiene_points,
                evidence=[evidence_paths.get("repo_hygiene"), evidence_paths.get("doctor_report")],
            )
    else:
        add_insufficient("missing_doctor_report", -10, [evidence_paths.get("doctor_report")])

    xp_total = max(0, int(xp_total))
    level, level_progress = _level_from_xp(xp_total)
    status = "INSUFFICIENT_DATA" if missing_reasons else "OK"

    source_artifacts = {
        "tournament_result": _safe_relpath(evidence_paths.get("tournament")),
        "judge_result": _safe_relpath(evidence_paths.get("judge")),
        "promotion_decision": _safe_relpath(evidence_paths.get("promotion")),
        "promotion_history_latest": _safe_relpath(evidence_paths.get("promotion_history")),
        "promotion_history": _safe_relpath(evidence_paths.get("promotion_history_jsonl")),
        "walk_forward_result": _safe_relpath(evidence_paths.get("walk_forward")),
        "walk_forward_windows": _safe_relpath(evidence_paths.get("walk_forward_windows")),
        "no_lookahead_audit": _safe_relpath(evidence_paths.get("no_lookahead_audit")),
        "doctor_report": _safe_relpath(evidence_paths.get("doctor_report")),
        "repo_hygiene": _safe_relpath(evidence_paths.get("repo_hygiene")),
    }
    clean_source_artifacts = {k: v for k, v in source_artifacts.items() if v}

    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": created_utc,
        "run_id": run_id or "unknown",
        "xp_spec_version": XP_SPEC_VERSION,
        "status": status,
        "insufficient_data": bool(missing_reasons),
        "missing_reasons": missing_reasons,
        "xp_total": xp_total,
        "level": level,
        "level_progress": level_progress,
        "xp_breakdown": breakdown,
        "source_artifacts": clean_source_artifacts,
        "repo_root_rel": str(repo_root().name),
    }
