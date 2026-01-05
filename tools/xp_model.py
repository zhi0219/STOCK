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
    trade_activity_report: dict[str, Any] | None,
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

    search_scale_penalty = None
    if isinstance(promotion, dict):
        search_scale_penalty = promotion.get("search_scale_penalty")
    if isinstance(search_scale_penalty, (int, float)):
        penalty_points = -int(round(float(search_scale_penalty) * 10))
        penalty_points = _clamp(penalty_points, -20, 0)
        add_item(
            key="search_scale_penalty",
            label="Governance: search-scale penalty",
            value=round(float(search_scale_penalty), 4),
            points=penalty_points,
            evidence=[evidence_paths.get("promotion")],
            notes="Placeholder penalty derived from multiple-testing governance metadata.",
        )
    else:
        add_insufficient(
            "missing_search_scale_penalty",
            -5,
            [evidence_paths.get("promotion")],
        )

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

    if trade_activity_report and isinstance(trade_activity_report, dict):
        ta_status = str(trade_activity_report.get("status") or "UNKNOWN")
        violations = trade_activity_report.get("violations", [])
        violation_codes = []
        if isinstance(violations, list):
            for item in violations:
                if isinstance(item, dict):
                    violation_codes.append(str(item.get("code")))
                else:
                    violation_codes.append(str(item))
        if ta_status != "PASS" or violation_codes:
            add_item(
                key="overtrading_guardrails",
                label="Overtrading guardrails",
                value=", ".join(code for code in violation_codes if code) or ta_status,
                points=-12,
                evidence=[evidence_paths.get("trade_activity_report")],
                notes="Penalizes trade-activity violations or missing audit status.",
            )
        calibration = (
            trade_activity_report.get("calibration")
            if isinstance(trade_activity_report.get("calibration"), dict)
            else {}
        )
        regime_info = (
            trade_activity_report.get("regime")
            if isinstance(trade_activity_report.get("regime"), dict)
            else {}
        )
        budget_payload = (
            trade_activity_report.get("budget")
            if isinstance(trade_activity_report.get("budget"), dict)
            else {}
        )
        budget_values = (
            budget_payload.get("budget")
            if isinstance(budget_payload.get("budget"), dict)
            else {}
        )
        calibration_status = str(calibration.get("status") or "MISSING")
        calibration_sample = calibration.get("sample_size")
        min_samples = calibration.get("min_samples_per_regime")
        regime_label = regime_info.get("label") if isinstance(regime_info, dict) else None

        if calibration_status == "OK" and isinstance(budget_values, dict) and budget_values:
            over_budget = []
            trades_peak = trade_activity_report.get("trades_per_day_peak")
            turnover = trade_activity_report.get("turnover_gross")
            min_seconds = trade_activity_report.get("min_seconds_between_trades")
            cost_per_trade = trade_activity_report.get("cost_per_trade")
            max_trades = budget_values.get("max_trades_per_day")
            max_turnover = budget_values.get("max_turnover_per_day")
            min_allowed = budget_values.get("min_seconds_between_trades")
            max_cost = budget_values.get("max_cost_per_trade")
            if isinstance(trades_peak, (int, float)) and isinstance(max_trades, (int, float)) and trades_peak > max_trades:
                over_budget.append("trades_per_day")
            if isinstance(turnover, (int, float)) and isinstance(max_turnover, (int, float)) and turnover > max_turnover:
                over_budget.append("turnover")
            if isinstance(min_seconds, (int, float)) and isinstance(min_allowed, (int, float)) and min_seconds < min_allowed:
                over_budget.append("min_seconds_between_trades")
            if isinstance(cost_per_trade, (int, float)) and isinstance(max_cost, (int, float)) and cost_per_trade > max_cost:
                over_budget.append("cost_per_trade")
            over_text = ", ".join(over_budget) if over_budget else "within_budget"
            points = -12 if over_budget else 5
            add_item(
                key="overtrading_calibrated_budget",
                label="Overtrading vs calibrated budget",
                value=over_text,
                points=points,
                evidence=[
                    evidence_paths.get("trade_activity_report"),
                    evidence_paths.get("overtrading_calibration"),
                ],
                notes=f"Regime={regime_label or 'UNKNOWN'}",
            )
        else:
            add_insufficient(
                "missing_overtrading_calibration",
                -6,
                [
                    evidence_paths.get("trade_activity_report"),
                    evidence_paths.get("overtrading_calibration"),
                ],
            )

        if (
            isinstance(calibration_sample, (int, float))
            and isinstance(min_samples, (int, float))
            and calibration_status == "OK"
        ):
            sample_ok = calibration_sample >= min_samples
            points = 3 if sample_ok else -4
            add_item(
                key="regime_sample_confidence",
                label="Regime confidence / sample size",
                value=f"{regime_label or 'UNKNOWN'} n={int(calibration_sample)}/{int(min_samples)}",
                points=points,
                evidence=[
                    evidence_paths.get("overtrading_calibration"),
                    evidence_paths.get("regime_report"),
                ],
            )
        else:
            add_insufficient(
                "missing_regime_confidence",
                -4,
                [
                    evidence_paths.get("overtrading_calibration"),
                    evidence_paths.get("regime_report"),
                ],
            )
    else:
        add_insufficient("missing_trade_activity_report", -8, [evidence_paths.get("trade_activity_report")])

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
        "trade_activity_report": _safe_relpath(evidence_paths.get("trade_activity_report")),
        "overtrading_calibration": _safe_relpath(evidence_paths.get("overtrading_calibration")),
        "regime_report": _safe_relpath(evidence_paths.get("regime_report")),
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
