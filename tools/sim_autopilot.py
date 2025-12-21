from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


def _default_risk_config() -> Dict[str, float | int | str]:
    return {
        "mode": "NORMAL",  # NORMAL -> SAFE -> OBSERVE
        "max_orders_per_minute": 2,
        "max_notional_per_order": 1_000.0,
        "max_daily_loss": 100.0,
        "max_drawdown": 0.05,
        "min_interval_seconds": 30,
        "degrade_on_loss": True,
    }


def _kill_switch_path(cfg: Dict[str, object]) -> Path:
    risk_cfg = cfg.get("risk_guards", {}) or {}
    kill_switch = risk_cfg.get("kill_switch_path", "./Data/KILL_SWITCH")
    path = Path(str(kill_switch)).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def _kill_switch_enabled(cfg: Dict[str, object]) -> bool:
    risk_cfg = cfg.get("risk_guards", {}) or {}
    return bool(risk_cfg.get("kill_switch_enabled", True))


@dataclass
class RiskState:
    mode: str = "NORMAL"
    intent_times: List[datetime] = field(default_factory=list)
    last_exec_ts: Optional[datetime] = None
    rejects_recent: List[str] = field(default_factory=list)
    daily_loss: float = 0.0
    start_equity: float = 10_000.0
    peak_equity: float = 10_000.0
    equity: float = 10_000.0
    postmortem_triggered: bool = False
    evidence_notes: List[str] = field(default_factory=list)

    def record_reject(self, reason: str) -> None:
        self.rejects_recent.append(reason)
        self.rejects_recent = self.rejects_recent[-10:]

    def register_intent(self, ts: datetime) -> None:
        self.intent_times.append(ts)
        cutoff = ts - timedelta(minutes=1)
        self.intent_times = [t for t in self.intent_times if t >= cutoff]

    def register_fill(self, pnl: float) -> None:
        self.daily_loss += max(-pnl, 0.0)
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    @property
    def risk_budget_used(self) -> float:
        if self.max_daily_loss <= 0:  # type: ignore[attr-defined]
            return 0.0
        return min(1.0, self.daily_loss / float(self.max_daily_loss))  # type: ignore[attr-defined]

    @property
    def drawdown_used(self) -> float:
        if self.max_drawdown <= 0:  # type: ignore[attr-defined]
            return 0.0
        return min(1.0, self.drawdown / float(self.max_drawdown))  # type: ignore[attr-defined]


class RiskEngine:
    def __init__(self, cfg: Dict[str, object], kill_switch_cfg: Dict[str, object], state: RiskState) -> None:
        merged = _default_risk_config()
        merged.update(cfg or {})
        self.cfg = merged
        self.kill_switch_cfg = kill_switch_cfg
        self.state = state
        self.state.max_daily_loss = float(self.cfg.get("max_daily_loss", 0.0))  # type: ignore[assignment]
        self.state.max_drawdown = float(self.cfg.get("max_drawdown", 0.0))  # type: ignore[assignment]

    def _data_bad(self, status: Optional[Dict[str, object]]) -> bool:
        if not status:
            return False
        flags = set()

        def _collect(value: object) -> None:
            if value is None:
                return
            if isinstance(value, str):
                flags.add(value.upper())
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    _collect(item)
            elif isinstance(value, dict):
                for val in value.values():
                    _collect(val)

        _collect(status.get("data_status"))
        _collect(status.get("data_flags"))
        _collect(status.get("data_health"))
        _collect((status.get("quotes") or {}).get("state"))
        _collect((status.get("quotes") or {}).get("health"))
        bad_markers = {"DATA_STALE", "DATA_MISSING", "DATA_SUSPECT", "DATA_FLAT"}
        return any(flag in bad_markers for flag in flags)

    def _check_rate_limit(self, now_ts: datetime) -> Optional[str]:
        max_per_minute = int(self.cfg.get("max_orders_per_minute", 0))
        if max_per_minute <= 0:
            return None
        self.state.register_intent(now_ts)
        if len(self.state.intent_times) > max_per_minute:
            return f"rate limit {len(self.state.intent_times)}/{max_per_minute} intents in 60s"
        return None

    def _check_min_interval(self, now_ts: datetime) -> Optional[str]:
        min_interval = float(self.cfg.get("min_interval_seconds", 0.0))
        if self.state.last_exec_ts and (now_ts - self.state.last_exec_ts).total_seconds() < min_interval:
            return f"min interval {min_interval}s not satisfied"
        return None

    def _check_notional(self, intent: Dict[str, object]) -> Optional[str]:
        max_notional = float(self.cfg.get("max_notional_per_order", 0.0))
        if max_notional <= 0:
            return None
        qty = float(intent.get("qty") or 0.0)
        price = float(intent.get("price") or 0.0)
        notional = abs(qty * price)
        if notional > max_notional:
            return f"notional {notional} exceeds max {max_notional}"
        return None

    def _check_kill_switch(self) -> Optional[str]:
        if not _kill_switch_enabled(self.kill_switch_cfg):
            return None
        if _kill_switch_path(self.kill_switch_cfg).expanduser().resolve().exists():
            return "kill switch engaged"
        return None

    def _check_loss_limits(self) -> Optional[str]:
        if self.state.daily_loss > float(self.cfg.get("max_daily_loss", 0.0)):
            return f"daily loss {self.state.daily_loss} exceeds max"
        if self.state.drawdown > float(self.cfg.get("max_drawdown", 0.0)):
            return f"drawdown {self.state.drawdown:.3f} exceeds max"
        return None

    def evaluate(self, intent: Dict[str, object], status: Optional[Dict[str, object]], now_ts: Optional[datetime] = None) -> Tuple[str, Optional[str]]:
        now_ts = now_ts or _now()
        checks = [
            ("DATA", self._data_bad(status)),
            ("KILL", bool(self._check_kill_switch())),
        ]
        for label, flag in checks:
            if flag:
                reason = "data gate" if label == "DATA" else "kill switch"
                self.state.record_reject(reason)
                return "RISK_REJECT", reason

        for check_fn in (self._check_rate_limit, self._check_min_interval):
            reason = check_fn(now_ts)
            if reason:
                self.state.record_reject(reason)
                return "RISK_REJECT", reason

        reason = self._check_notional(intent)
        if reason:
            self.state.record_reject(reason)
            return "RISK_REJECT", reason

        loss_reason = self._check_loss_limits()
        if loss_reason:
            self.state.record_reject(loss_reason)
            return "RISK_REJECT", loss_reason

        if self.state.mode == "OBSERVE":
            self.state.record_reject("observe mode")
            return "OBSERVE", "observe mode"
        if self.state.mode == "SAFE":
            self.state.record_reject("safe mode")
            return "SAFE", "safe mode"
        return "ALLOW", None


class SimAutopilot:
    def __init__(self, config_path: Optional[Path] = None, logs_dir: Optional[Path] = None, risk_overrides: Optional[Dict[str, object]] = None) -> None:
        self.root = Path(__file__).resolve().parent.parent
        self.config_path = config_path or (self.root / "config.yaml")
        cfg = self._load_config()
        self.logs_dir = (logs_dir or (self.root / "Logs")).expanduser().resolve()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.orders_path = self.logs_dir / "orders_sim.jsonl"
        self.events_path = self.logs_dir / "events_sim.jsonl"
        risk_cfg = (cfg.get("sim_risk") or {}) if isinstance(cfg, dict) else {}
        if risk_overrides:
            risk_cfg.update(risk_overrides)
        self.state = RiskState(mode=str(risk_cfg.get("mode", "NORMAL")).upper())
        self.risk_engine = RiskEngine(risk_cfg, cfg or {}, self.state)
        self.sim_fill = {"slippage_bps": 3.0, "fee_usd": 0.25, "latency_sec": 0.4}

    def _load_config(self) -> Dict[str, object]:
        if not self.config_path.exists():
            return {}
        try:
            with self.config_path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except Exception:
            return {}

    def _persist_risk_state(self) -> None:
        payload = {
            "mode": self.state.mode,
            "risk_budget_used": round(self.state.risk_budget_used, 4),
            "drawdown_used": round(self.state.drawdown_used, 4),
            "rejects_recent": list(self.state.rejects_recent),
            "ts_utc": _now().isoformat(),
        }
        risk_state_path = self.logs_dir / "risk_state.json"
        risk_state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_event(self, event: Dict[str, object]) -> None:
        event = dict(event)
        event.setdefault("ts_utc", _now().isoformat())
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _order_line_no(self) -> int:
        if not self.orders_path.exists():
            return 1
        try:
            with self.orders_path.open("r", encoding="utf-8") as fh:
                return sum(1 for _ in fh) + 1
        except Exception:
            return 1

    def _write_order(self, intent: Dict[str, object], now_ts: datetime) -> int:
        line_no = self._order_line_no()
        record = dict(intent)
        record.setdefault("ts_utc", now_ts.isoformat())
        record["sim_fill"] = dict(self.sim_fill)
        with self.orders_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return line_no

    def _trigger_postmortem(self, evidence: str, threshold_reason: str) -> None:
        if self.state.postmortem_triggered:
            return
        self.state.postmortem_triggered = True
        self.state.mode = "SAFE" if self.state.mode == "NORMAL" else "OBSERVE"
        event = {
            "event_type": "POSTMORTEM",
            "severity": "CRITICAL",
            "message": "Loss threshold breached; entering degraded mode",
            "metrics": {
                "daily_loss": self.state.daily_loss,
                "drawdown": self.state.drawdown,
                "max_daily_loss": self.risk_engine.cfg.get("max_daily_loss"),
                "max_drawdown": self.risk_engine.cfg.get("max_drawdown"),
            },
            "evidence": evidence,
            "threshold_reason": threshold_reason,
        }
        self.state.evidence_notes.append(evidence)
        self._append_event(event)

    def process_intent(self, intent: Dict[str, object], status: Optional[Dict[str, object]] = None, now_ts: Optional[datetime] = None) -> Tuple[str, Optional[str]]:
        now_ts = now_ts or _now()
        decision, reason = self.risk_engine.evaluate(intent, status, now_ts)
        if decision == "ALLOW":
            line_no = self._write_order(intent, now_ts)
            pnl = float(intent.get("pnl") or 0.0)
            self.state.register_fill(pnl)
            self.state.last_exec_ts = now_ts
            loss_reason = self.risk_engine._check_loss_limits()
            if loss_reason and self.risk_engine.cfg.get("degrade_on_loss", True):
                evidence = f"orders_sim.jsonl#L{line_no} {loss_reason}"
                status_flag = status.get("data_status") if status else "status?"
                evidence = f"{evidence}; status={status_flag}"
                self._trigger_postmortem(evidence, loss_reason)
        elif decision in {"SAFE", "OBSERVE"}:
            self._append_event(
                {
                    "event_type": "SIM_INTENT",
                    "severity": "INFO",
                    "message": f"Intent only due to mode {self.state.mode}",
                    "intent": intent,
                }
            )
        self._persist_risk_state()
        return decision, reason


__all__ = ["SimAutopilot", "RiskEngine", "RiskState"]
