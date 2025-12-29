from __future__ import annotations

import os
import json
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

import yaml

try:
    import tkinter as tk
    from tkinter import messagebox
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText
except Exception:
    print("tkinter is required to run this UI. Please ensure Tk is installed.")
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
STATE_PATH = LOGS_DIR / "supervisor_state.json"
SUPERVISOR_SCRIPT = ROOT / "tools" / "supervisor.py"
QA_FLOW_SCRIPT = ROOT / "tools" / "qa_flow.py"
CAPTURE_ANSWER_SCRIPT = ROOT / "tools" / "capture_ai_answer.py"
TRAIN_DAEMON_SCRIPT = ROOT / "tools" / "train_daemon.py"
TRAIN_SERVICE_SCRIPT = ROOT / "tools" / "train_service.py"
UI_LOG_PATH = ROOT / "Logs" / "ui_actions.log"
CONFIG_PATH = ROOT / "config.yaml"
SERVICE_STATE_PATH = ROOT / "Logs" / "train_service" / "state.json"
SERVICE_KILL_SWITCH = ROOT / "Logs" / "train_service" / "KILL_SWITCH"
SERVICE_ROLLING_SUMMARY = ROOT / "Logs" / "train_service" / "rolling_summary.md"
PROGRESS_INDEX_PATH = ROOT / "Logs" / "train_runs" / "progress_index.json"
PROGRESS_INDEX_SCRIPT = ROOT / "tools" / "progress_index.py"
THROUGHPUT_DIAG_SCRIPT = ROOT / "tools" / "progress_throughput_diagnose.py"
THROUGHPUT_DIAG_REPORT = ROOT / "Logs" / "train_service" / "throughput_diagnose_latest.txt"
ACTION_CENTER_REPORT_PATH = ROOT / "artifacts" / "action_center_report.json"
ACTION_CENTER_REPORT_MODULE = "tools.action_center_report"
ACTION_CENTER_APPLY_MODULE = "tools.action_center_apply"
ACTION_CENTER_APPLY_RESULT_PATH = ROOT / "artifacts" / "action_center_apply_result.json"
DOCTOR_REPORT_PATH = ROOT / "artifacts" / "doctor_report.json"
ARTIFACTS_DIR = ROOT / "artifacts"
LATEST_DIR = ROOT / "Logs" / "train_runs" / "_latest"
PROGRESS_JUDGE_LATEST_PATH = LATEST_DIR / "progress_judge_latest.json"
LEGACY_PROGRESS_JUDGE_LATEST_PATH = ROOT / "Logs" / "train_runs" / "progress_judge" / "latest.json"
LATEST_POLICY_HISTORY_PATH = LATEST_DIR / "policy_history_latest.json"
LATEST_PROMOTION_DECISION_PATH = LATEST_DIR / "promotion_decision_latest.json"
LATEST_TOURNAMENT_PATH = LATEST_DIR / "tournament_latest.json"
PR28_TOURNAMENT_RESULT_LATEST_PATH = LATEST_DIR / "tournament_result_latest.json"
PR28_JUDGE_RESULT_LATEST_PATH = LATEST_DIR / "judge_result_latest.json"
PR28_PROMOTION_DECISION_LATEST_PATH = LATEST_DIR / "promotion_decision_latest.json"
PR28_PROMOTION_HISTORY_LATEST_PATH = LATEST_DIR / "promotion_history_latest.json"
LATEST_STRESS_REPORT_PATH = LATEST_DIR / "stress_report_latest.json"
FRICTION_POLICY_PATH = ROOT / "Data" / "friction_policy.json"
XP_SNAPSHOT_DIR = ROOT / "Logs" / "train_runs" / "progress_xp"
XP_SNAPSHOT_LATEST_PATH = XP_SNAPSHOT_DIR / "xp_snapshot_latest.json"
UI_SMOKE_LATEST_PATH = ROOT / "Logs" / "ui_smoke_latest.json"
POLICY_REGISTRY_PATH = policy_registry_runtime_path()
BASELINE_GUIDE_SCRIPT = ROOT / "tools" / "baseline_fix_guide.py"
BASELINE_GUIDE_PATH = ROOT / "Logs" / "baseline_guide.txt"
JUDGE_STALE_SECONDS = 3600
CADENCE_LABELS = {
    "Micro (high frequency)": "micro",
    "Normal": "normal",
    "Conservative": "conservative",
}
ACTION_CENTER_DEFAULTS = {
    "CLEAR_KILL_SWITCH": {
        "title": "Clear Kill Switch (SIM-only)",
        "confirmation_token": "CLEAR",
        "safety_notes": "SIM-only. Removes local kill switch files and does not place trades.",
        "effect_summary": "Clears kill switch files via supervisor clear-kill-switch.",
        "risk_level": "CAUTION",
    },
    "ACTION_REBUILD_PROGRESS_INDEX": {
        "title": "Rebuild Progress Index",
        "confirmation_token": "REBUILD",
        "safety_notes": "SIM-only. Regenerates Logs/train_runs/progress_index.json from local files.",
        "effect_summary": "Runs tools/progress_index.py to refresh the progress index.",
        "risk_level": "CAUTION",
    },
    "ACTION_RESTART_SERVICES_SIM_ONLY": {
        "title": "Restart SIM Services",
        "confirmation_token": "RESTART",
        "safety_notes": "SIM-only. Restarts local supervisor-managed services; no broker access.",
        "effect_summary": "Stops and starts supervisor services (quotes/alerts).",
        "risk_level": "CAUTION",
    },
    "GEN_DOCTOR_REPORT": {
        "title": "Generate Doctor report",
        "confirmation_token": "RUN",
        "safety_notes": "SIM-only. Writes artifacts/doctor_report.json for diagnostics.",
        "effect_summary": "Runs tools.doctor_report to capture health evidence.",
        "risk_level": "SAFE",
    },
    "REPO_HYGIENE_FIX_SAFE": {
        "title": "Repo hygiene fix (safe)",
        "confirmation_token": "HYGIENE",
        "safety_notes": "SIM-only. Restores tracked runtime artifacts and removes untracked runtime files.",
        "effect_summary": "Runs python -m tools.repo_hygiene fix --mode safe.",
        "risk_level": "CAUTION",
    },
    "CLEAR_STALE_TEMP": {
        "title": "Clear stale temp files",
        "confirmation_token": "CLEAN",
        "safety_notes": "SIM-only. Deletes stale *.tmp files older than Doctor threshold.",
        "effect_summary": "Removes stale temp files from Logs/runtime and Logs/.",
        "risk_level": "CAUTION",
    },
    "ENSURE_RUNTIME_DIRS": {
        "title": "Ensure runtime directories",
        "confirmation_token": "MKDIR",
        "safety_notes": "SIM-only. Creates runtime directories if missing.",
        "effect_summary": "Creates Logs/runtime, Logs/train_service, and artifacts directories.",
        "risk_level": "SAFE",
    },
    "DIAG_RUNTIME_WRITE": {
        "title": "Diagnose runtime write",
        "confirmation_token": "DIAG",
        "safety_notes": "SIM-only. Re-runs runtime write checks and stores results.",
        "effect_summary": "Writes artifacts/doctor_runtime_write.json for evidence.",
        "risk_level": "SAFE",
    },
    "ABS_PATH_SANITIZE_HINT": {
        "title": "Generate absolute-path sanitization hints",
        "confirmation_token": "SANITIZE",
        "safety_notes": "SIM-only. Writes a sanitized-paths guidance artifact.",
        "effect_summary": "Writes artifacts/abs_path_sanitize_hint.json with guidance.",
        "risk_level": "SAFE",
    },
    "ENABLE_GIT_HOOKS": {
        "title": "Enable git hooks (best effort)",
        "confirmation_token": "HOOKS",
        "safety_notes": "SIM-only. Best-effort enable githooks for repo hygiene.",
        "effect_summary": "Runs scripts/enable_githooks.* if available.",
        "risk_level": "SAFE",
    },
    "RUN_RETENTION_REPORT": {
        "title": "Run retention report",
        "confirmation_token": "REPORT",
        "safety_notes": "SIM-only. Generates retention report for storage health evidence.",
        "effect_summary": "Runs python -m tools.retention_engine report.",
        "risk_level": "SAFE",
    },
    "PRUNE_OLD_RUNS_SAFE": {
        "title": "Prune old runs (safe)",
        "confirmation_token": "PRUNE",
        "safety_notes": "SIM-only. Conservative retention prune with safety checks.",
        "effect_summary": "Runs python -m tools.retention_engine prune --mode safe.",
        "risk_level": "SAFE",
    },
    "REBUILD_RECENT_INDEX": {
        "title": "Rebuild recent runs index",
        "confirmation_token": "INDEX",
        "safety_notes": "SIM-only. Rebuilds Logs/train_runs/recent_runs_index.json.",
        "effect_summary": "Runs python -m tools.recent_runs_index.",
        "risk_level": "SAFE",
    },
    "FIX_GIT_RED_SAFE": {
        "title": "Fix Git Red (Safe)",
        "confirmation_token": "GITSAFE",
        "safety_notes": "SIM-only. Restores tracked runtime artifacts and removes untracked runtime files.",
        "effect_summary": "Applies safe git hygiene fixes and writes evidence artifacts.",
        "risk_level": "SAFE",
    },
    "REVIEW_GIT_DIRTY": {
        "title": "Review unknown git changes",
        "confirmation_token": "REVIEW",
        "safety_notes": "SIM-only. Generates guidance for unknown git changes; no auto-apply.",
        "effect_summary": "Writes a review guidance artifact for manual inspection.",
        "risk_level": "CAUTION",
    },
}

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.git_baseline_probe import probe_baseline
from tools.paths import policy_registry_runtime_path
from tools.paths import to_repo_relative
from tools.train_service import CADENCE_PRESETS
from tools.ui_parsers import (
    load_decision_cards,
    load_engine_status,
    load_policy_history,
    load_policy_history_latest,
    load_pr28_latest,
    load_progress_judge_latest,
    load_replay_index_latest,
    load_xp_snapshot_latest,
)
from tools.ui_scroll import VerticalScrolledFrame

try:
    from tools import explain_now
    from tools.dashboard_model import (
        compute_event_rows,
        compute_health,
        compute_move_leaderboard,
        load_latest_status,
        load_recent_events,
    )
    from tools.progress_diagnose import compute_progress_diagnosis
    from tools.progress_plot import compute_polyline
    from tools.stdio_utf8 import configure_stdio_utf8
    from tools.wakeup_dashboard import (
        MISSING_FIELD_TEXT,
        find_latest_run_dir,
        find_latest_summary_md,
        parse_summary_key_fields,
    )
    from tools.train_service_hud import TrainingHudSnapshot, compute_training_hud
except Exception:
    explain_now = None
    compute_event_rows = None  # type: ignore[assignment]
    compute_health = None  # type: ignore[assignment]
    compute_move_leaderboard = None  # type: ignore[assignment]
    load_latest_status = None  # type: ignore[assignment]
    load_recent_events = None  # type: ignore[assignment]
    compute_progress_diagnosis = None  # type: ignore[assignment]
    compute_polyline = None  # type: ignore[assignment]
    configure_stdio_utf8 = None  # type: ignore[assignment]
    MISSING_FIELD_TEXT = "字段缺失/版本差异"  # type: ignore[assignment]
    find_latest_run_dir = None  # type: ignore[assignment]
    find_latest_summary_md = None  # type: ignore[assignment]
    parse_summary_key_fields = None  # type: ignore[assignment]
    TrainingHudSnapshot = None  # type: ignore[assignment]
    compute_training_hud = None  # type: ignore[assignment]


def _utf8_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _load_ui_smoke_latest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_kill_switch_path(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    risk_cfg = cfg.get("risk_guards", {}) or {}
    kill_switch = risk_cfg.get("kill_switch_path", "./Data/KILL_SWITCH")
    return ROOT / str(kill_switch)


def read_text_tail(path: Path, lines: int = 20) -> str:
    if not path or not path.exists():
        return "(no events file)"
    try:
        with path.open("r", encoding="utf-8") as fh:
            content = fh.readlines()
        return "".join(content[-lines:]) if content else "(empty)"
    except Exception as exc:  # pragma: no cover - UI feedback
        return f"error reading {path}: {exc}"


def latest_events_file() -> Path | None:
    candidates = sorted((ROOT / "Logs").glob("events_*.jsonl"))
    return candidates[-1] if candidates else None


def _format_age(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_iso_timestamp(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _last_kill_switch_event(logs_dir: Path) -> tuple[datetime | None, str]:
    candidates: list[Path] = []
    latest = latest_events_file()
    if latest:
        candidates.append(latest)
    train_events = logs_dir / "events_train.jsonl"
    if train_events.exists():
        candidates.append(train_events)

    last_ts: datetime | None = None
    last_source = ""
    for path in candidates:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            if str(event.get("event_type")) not in {"KILL_SWITCH", "TRAIN_STOPPED_KILL_SWITCH"}:
                continue
            ts = parse_iso_timestamp(event.get("ts_utc"))
            if ts is None:
                continue
            if last_ts is None or ts > last_ts:
                last_ts = ts
                last_source = path.name
                break
    return last_ts, last_source


def run_supervisor_command(commands: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SUPERVISOR_SCRIPT), *commands],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
    )


@dataclass
class RunResult:
    command: List[str]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str
    note: str = ""

    def format_lines(self) -> str:
        lines = [
            f"Command: {' '.join(self.command)}",
            f"CWD: {self.cwd}",
            f"Exit code: {self.returncode}",
            "--- stdout ---",
            (self.stdout or "(empty)").rstrip(),
            "--- stderr ---",
            (self.stderr or "(empty)").rstrip(),
        ]
        if self.note:
            lines.append(f"Note: {self.note}")
        return "\n".join(lines)


def _append_ui_log(content: str) -> None:
    UI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with UI_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {content}\n")


def run_verify_script(script_name: str) -> RunResult:
    script_path = ROOT / "tools" / script_name
    note = ""
    kill_switch = get_kill_switch_path()
    if kill_switch.exists():
        try:
            kill_switch.unlink()
            note = "Removed stale KILL_SWITCH before verify"
        except Exception as exc:
            note = f"KILL_SWITCH present and could not be removed: {exc}"

    command = [sys.executable, str(script_path)]
    proc = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
    )
    stdout = proc.stdout or ""
    if note:
        stdout = f"{note}\n{stdout}" if stdout else note
    result = RunResult(command, ROOT, proc.returncode, stdout, proc.stderr or "", note)
    _append_ui_log(result.format_lines())
    return result


def parse_training_markers(text: str) -> dict[str, str]:
    markers: dict[str, str] = {}
    for line in text.splitlines():
        for key in ("RUN_DIR", "STOP_REASON", "SUMMARY_PATH"):
            prefix = f"{key}="
            if line.startswith(prefix):
                markers[key] = line.split("=", 1)[1].strip()
    return markers


def run_training_daemon(
    max_runtime_seconds: int,
    input_path: Path | None = None,
    runs_root: Path | None = None,
    retain_days: int | None = None,
    retain_latest_n: int | None = None,
    max_total_train_runs_mb: int | None = None,
    nightly: bool = False,
) -> tuple[RunResult, dict[str, str]]:
    command = [
        sys.executable,
        str(TRAIN_DAEMON_SCRIPT),
        "--max-runtime-seconds",
        str(max_runtime_seconds),
    ]
    if nightly:
        command.append("--nightly")
    if input_path:
        command.extend(["--input", str(input_path)])
    if runs_root:
        command.extend(["--runs-root", str(runs_root)])
    if retain_days is not None:
        command.extend(["--retain-days", str(retain_days)])
    if retain_latest_n is not None:
        command.extend(["--retain-latest-n", str(retain_latest_n)])
    if max_total_train_runs_mb is not None:
        command.extend(["--max-total-train-runs-mb", str(max_total_train_runs_mb)])
    proc = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    markers = parse_training_markers(stdout) or parse_training_markers(stderr)
    result = RunResult(command, ROOT, proc.returncode, stdout, stderr)
    return result, markers


def latest_training_summary() -> tuple[Path | None, Path | None]:
    base = LOGS_DIR / "train_runs"
    if not base.exists():
        return None, None
    candidates: list[tuple[float, Path]] = []
    for summary in base.glob("**/summary.md"):
        try:
            mtime = summary.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, summary))
    if not candidates:
        return None, None
    latest_summary = sorted(candidates, key=lambda pair: pair[0])[-1][1]
    return latest_summary.parent, latest_summary


def load_state_text() -> str:
    if not STATE_PATH.exists():
        return "state file not found"
    try:
        return STATE_PATH.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - UI feedback
        return f"error reading state: {exc}"


def load_service_state() -> dict:
    if not SERVICE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(SERVICE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _service_running(state: dict) -> bool:
    hb = state.get("last_heartbeat_ts")
    try:
        if hb:
            ts = ensure_aware_utc(parse_iso_timestamp(hb))
            if ts is None:
                return False
            return (utc_now() - ts).total_seconds() < 120 and not state.get("stop_reason")
    except Exception:
        return False
    return False


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("STOCK Supervisor")
        width = int(self.winfo_screenwidth() * 0.9)
        height = int(self.winfo_screenheight() * 0.9)
        self.geometry(f"{width}x{height}")
        self._lock = threading.Lock()
        self._qa_output_queue: "queue.Queue[str]" = queue.Queue()
        self._verify_output_queue: "queue.Queue[str]" = queue.Queue()
        self._summary_queue: "queue.Queue[str]" = queue.Queue()
        self._training_output_queue: "queue.Queue[str]" = queue.Queue()
        self._events_cache: List[dict] = []
        self._events_rows: dict[str, dict] = {}
        self.last_packet_path: Path | None = None
        self.last_answer_path: Path | None = None
        self._latest_training_run_dir: Path | None = None
        self._latest_training_summary_path: Path | None = None
        self._hud_last_summary_rendered: Path | None = None
        self._latest_wakeup_run_dir: Path | None = None
        self._latest_wakeup_summary_path: Path | None = None
        self.progress_index_path = PROGRESS_INDEX_PATH
        self.progress_entries: list[dict[str, object]] = []
        self.progress_selected_entry: dict[str, object] | None = None
        self.progress_status_var = tk.StringVar(value="Progress index: not loaded")
        self.progress_detail_status_var = tk.StringVar(value="Status: -")
        self.progress_detail_missing_var = tk.StringVar(value="Missing reason: -")
        self.progress_judge_xp_var = tk.StringVar(value="Truthful XP: No judge data")
        self.progress_judge_level_var = tk.StringVar(value="Level: No judge data")
        self.progress_xp_status_var = tk.StringVar(value="Truthful XP: INSUFFICIENT_DATA")
        self.progress_xp_level_var = tk.StringVar(value="Level: -")
        self.progress_xp_banner_var = tk.StringVar(value="")
        self.progress_xp_evidence_var = tk.StringVar(value="Evidence paths: -")
        self.progress_truth_status_var = tk.StringVar(value="Truthful Progress: INSUFFICIENT_DATA")
        self.progress_truth_score_do_nothing_var = tk.StringVar(value="Score vs DoNothing: -")
        self.progress_truth_score_buy_hold_var = tk.StringVar(value="Score vs Buy&Hold: -")
        self.progress_truth_trend_var = tk.StringVar(value="Trend: unknown")
        self.progress_truth_why_var = tk.StringVar(value="Why: -")
        self.progress_truth_not_improving_var = tk.StringVar(value="Not improving because: -")
        self.progress_truth_action_var = tk.StringVar(value="Suggested action: -")
        self.progress_truth_evidence_var = tk.StringVar(value="Evidence: -")
        self.progress_friction_policy_var = tk.StringVar(value="Friction policy: -")
        self.progress_friction_detail_var = tk.StringVar(value="Friction settings: -")
        self.progress_stress_status_var = tk.StringVar(value="Stress status: -")
        self.progress_stress_reject_var = tk.StringVar(value="Stress reject reasons: -")
        self.progress_stress_evidence_var = tk.StringVar(value="Stress evidence: -")
        self.progress_diag_status_var = tk.StringVar(value="Diagnosis: -")
        self.progress_diag_summary_var = tk.StringVar(value="Progress diagnosis will appear here.")
        self.progress_growth_total_var = tk.StringVar(value="Total runs: -")
        self.progress_growth_runs_today_var = tk.StringVar(value="Runs today: - | Last run: -")
        self.progress_growth_last_net_var = tk.StringVar(value="Last run net: -")
        self.progress_growth_cash_last_var = tk.StringVar(value="Δcash (last run): -")
        self.progress_growth_cash_24h_var = tk.StringVar(value="Δcash (24h): -")
        self.progress_growth_seven_day_var = tk.StringVar(value="7-day net: -")
        self.progress_growth_max_dd_var = tk.StringVar(value="Max drawdown (last): -")
        self.progress_growth_rejects_var = tk.StringVar(value="Rejects: -")
        self.progress_growth_gates_var = tk.StringVar(value="Gates triggered: -")
        self.progress_growth_service_var = tk.StringVar(value="Service: -")
        self.progress_growth_kill_var = tk.StringVar(value="Kill switch: -")
        self.progress_growth_truth_xp_var = tk.StringVar(value="Truthful XP: -")
        self._xp_snapshot_cache: dict[str, object] | None = None
        self.engine_status_tournament_var = tk.StringVar(value="Last tournament updated: -")
        self.engine_status_promotion_var = tk.StringVar(value="Last promotion decision: -")
        self.engine_status_judge_var = tk.StringVar(value="Last judge updated: -")
        self.pr28_tournament_status_var = tk.StringVar(value="PR28 tournament: not loaded")
        self.pr28_judge_status_var = tk.StringVar(value="PR28 judge: not loaded")
        self.pr28_promotion_status_var = tk.StringVar(value="PR28 promotion: not loaded")
        self.pr28_evidence_var = tk.StringVar(value="PR28 evidence: -")
        self.skill_tree_status_var = tk.StringVar(value="Skill Tree: not loaded")
        self.skill_tree_detail_var = tk.StringVar(value="Pool summary: -")
        self.skill_tree_candidates_var = tk.StringVar(value="Candidates: -")
        self.upgrade_log_status_var = tk.StringVar(value="Upgrade Log: not loaded")
        self.upgrade_log_entries: list[dict[str, object]] = []
        self.policy_history_status_var = tk.StringVar(value="Policy History: latest not loaded")
        self.progress_curve_mode_var = tk.StringVar(value="Last N runs (concat)")
        self.progress_curve_runs_var = tk.IntVar(value=10)
        self.progress_equity_stats_var = tk.StringVar(value="Start: - | End: - | Net: - | Max DD: -")
        self.progress_auto_refresh_var = tk.BooleanVar(value=True)
        self.progress_refresh_interval_var = tk.IntVar(value=5)
        self.progress_last_refresh_var = tk.StringVar(value="Last refresh: -")
        self.progress_last_new_run_var = tk.StringVar(value="Last new run: -")
        self.progress_run_rate_var = tk.StringVar(value="Run-rate: -")
        self.progress_run_stale_var = tk.StringVar(value="Stale: -")
        self.progress_throughput_diag_var = tk.StringVar(value="Throughput diagnosis: -")
        self.proof_baseline_status_var = tk.StringVar(value="Baseline: unknown")
        self.proof_baseline_detail_var = tk.StringVar(value="Reason: -")
        self.proof_service_status_var = tk.StringVar(value="Training Service: unknown")
        self.proof_service_detail_var = tk.StringVar(value="Heartbeat: -")
        self.proof_judge_status_var = tk.StringVar(value="Judge: unknown")
        self.proof_judge_detail_var = tk.StringVar(value="Updated: -")
        self.proof_ui_smoke_status_var = tk.StringVar(value="UI Smoke: unknown")
        self.proof_ui_smoke_detail_var = tk.StringVar(value="Updated: -")
        self.proof_baseline_lamp: tk.Label | None = None
        self.proof_service_lamp: tk.Label | None = None
        self.proof_judge_lamp: tk.Label | None = None
        self.proof_ui_smoke_lamp: tk.Label | None = None
        self.policy_history_entries: list[dict[str, object]] = []
        self.hud_mode_detail_var = tk.StringVar(value="Status: unknown")
        self.hud_kill_switch_var = tk.StringVar(value="Kill switch: unknown")
        self.hud_kill_switch_detail_var = tk.StringVar(value="Kill switch paths: -")
        self.hud_kill_switch_trip_var = tk.StringVar(value="Last trip: -")
        self.hud_data_health_var = tk.StringVar(value="Data health: unknown")
        self.hud_stage_var = tk.StringVar(value="Stage: -")
        self.hud_run_id_var = tk.StringVar(value="Run: (none)")
        self.hud_elapsed_var = tk.StringVar(value="Elapsed: -")
        self.hud_next_iter_var = tk.StringVar(value="Next iteration: -")
        self.hud_budget_iter_var = tk.StringVar(value="Episodes/day: -")
        self.hud_budget_hour_var = tk.StringVar(value="Episodes/hour: -")
        self.hud_budget_disk_var = tk.StringVar(value="Disk budget MB: -")
        self.hud_max_dd_var = tk.StringVar(value="Max drawdown: -")
        self.hud_turnover_var = tk.StringVar(value="Turnover: -")
        self.hud_rejects_var = tk.StringVar(value="Rejects: -")
        self.hud_gates_var = tk.StringVar(value="Gates triggered: -")
        self.hud_equity_var = tk.StringVar(value="Equity delta: -")
        self.service_status_var = tk.StringVar(value="Service: unknown")
        self.service_run_dir_var = tk.StringVar(value="Last run: (none)")
        self.service_summary_var = tk.StringVar(value="Last summary: (none)")
        self.service_episode_seconds_var = tk.StringVar(value="300")
        self.service_max_hour_var = tk.StringVar(value="12")
        self.service_max_day_var = tk.StringVar(value="200")
        self.service_cooldown_var = tk.StringVar(value="10")
        self.service_max_steps_var = tk.StringVar(value="5000")
        self.service_max_trades_var = tk.StringVar(value="500")
        self.service_max_events_var = tk.StringVar(value="300")
        self.service_max_disk_var = tk.StringVar(value="5000")
        self.service_max_runtime_day_var = tk.StringVar(value="28800")
        self.service_cadence_preset_var = tk.StringVar(value="Micro (high frequency)")
        self._progress_last_refresh_ts: float | None = None
        self._progress_last_new_run_ts: datetime | None = None
        self._progress_last_run_id: str | None = None
        self._progress_last_index_mtime: float | None = None
        self._progress_last_latest_mtime: float | None = None
        self._action_center_last_mtime: float | None = None
        self._action_center_report: dict[str, object] | None = None
        self._action_center_actions: list[dict[str, object]] = []
        self.replay_status_var = tk.StringVar(value="Replay: missing")
        self.replay_detail_var = tk.StringVar(value="Latest replay: -")
        self.replay_latest_path_var = tk.StringVar(value="decision_cards_latest.jsonl: -")
        self.replay_action_filter_var = tk.StringVar(value="(all)")
        self.replay_symbol_filter_var = tk.StringVar(value="")
        self.replay_reject_only_var = tk.BooleanVar(value=False)
        self.replay_guard_failed_var = tk.BooleanVar(value=False)
        self.replay_selected_detail_var = tk.StringVar(value="Selected card: (none)")
        self._replay_cards: list[dict[str, object]] = []
        self._replay_filtered_cards: list[dict[str, object]] = []
        self._replay_index_payload: dict[str, object] | None = None
        self.action_center_selected_var = tk.StringVar(value="Selected action: (none)")
        self.action_center_status_marker_var = tk.StringVar(value="ACTION_CENTER_STATUS: ISSUE")
        self.action_center_data_health_var = tk.StringVar(value="DATA_HEALTH: ISSUE")
        self.action_center_last_report_var = tk.StringVar(value="LAST_REPORT_TS_UTC: -")
        self.action_center_last_apply_var = tk.StringVar(value="LAST_APPLY_TS_UTC: -")
        self.action_center_doctor_status_var = tk.StringVar(value="DOCTOR_STATUS: -")
        self.action_center_last_doctor_var = tk.StringVar(value="LAST_DOCTOR_TS_UTC: -")
        self.action_center_evidence_path_var = tk.StringVar(value=str(ARTIFACTS_DIR))
        self.action_center_show_advanced_var = tk.BooleanVar(value=False)
        self._scroll_frames: list[VerticalScrolledFrame] = []
        self._apply_cadence_preset_fields(CADENCE_LABELS.get(self.service_cadence_preset_var.get(), "micro"))
        self._build_ui()
        self._start_auto_refresh()

    def _create_scrollable_tab(self, notebook: ttk.Notebook, title: str) -> tuple[ttk.Frame, tk.Frame]:
        outer = ttk.Frame(notebook)
        content: tk.Frame = outer
        try:
            scroller = VerticalScrolledFrame(outer)
            scroller.pack(fill=tk.BOTH, expand=True)
            content = scroller.interior
            self._scroll_frames.append(scroller)
        except Exception:
            content = outer
        notebook.add(outer, text=title)
        return outer, content

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True)

        _, self.run_tab = self._create_scrollable_tab(notebook, "Run")
        _, self.health_tab = self._create_scrollable_tab(notebook, "Dashboard")
        _, self.events_tab = self._create_scrollable_tab(notebook, "Events")
        _, self.progress_tab = self._create_scrollable_tab(notebook, "Progress (SIM-only)")
        _, self.replay_tab = self._create_scrollable_tab(notebook, "Replay (SIM-only)")
        _, self.action_center_tab = self._create_scrollable_tab(notebook, "Action Center")
        _, self.summary_tab = self._create_scrollable_tab(notebook, "摘要")
        _, self.qa_tab = self._create_scrollable_tab(notebook, "AI Q&A")
        _, self.verify_tab = self._create_scrollable_tab(notebook, "Verify")

        self._build_run_tab()
        self._build_health_tab()
        self._build_events_tab()
        self._build_progress_tab()
        self._build_replay_tab()
        self._build_action_center_tab()
        self._build_summary_tab()
        self._build_qa_panel()
        self._build_verify_tab()

        self.after(300, self._drain_qa_output)
        self.after(400, self._drain_verify_output)
        self.after(500, self._drain_summary_queue)
        self.after(450, self._drain_training_output)

    def _handle_start(self) -> None:
        kill_switch = get_kill_switch_path()
        if kill_switch.exists():
            self._show_kill_switch_prompt(kill_switch)
            return
        self._run_supervisor_async(["start"])

    def _handle_stop(self) -> None:
        self._run_supervisor_async(["stop"])

    def _handle_clear_kill_switch(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Clear Kill Switch (SIM-only)")
        dialog.grab_set()

        tk.Label(
            dialog,
            text="Type CLEAR to confirm removing kill switch files (SIM-only).",
            justify=tk.LEFT,
            wraplength=520,
        ).pack(anchor="w", padx=10, pady=10)

        entry_var = tk.StringVar()
        entry = tk.Entry(dialog, textvariable=entry_var, width=20)
        entry.pack(anchor="w", padx=10)
        entry.focus_set()

        button_frame = tk.Frame(dialog)
        button_frame.pack(pady=10)
        confirm_btn = tk.Button(button_frame, text="Clear Kill Switch", state=tk.DISABLED)
        confirm_btn.pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        def _on_change(*_args: object) -> None:
            if entry_var.get().strip().upper() == "CLEAR":
                confirm_btn.configure(state=tk.NORMAL)
            else:
                confirm_btn.configure(state=tk.DISABLED)

        def _on_confirm() -> None:
            dialog.destroy()
            self._run_supervisor_async(["clear-kill-switch"])

        entry_var.trace_add("write", _on_change)
        confirm_btn.configure(command=_on_confirm)

    def _show_kill_switch_prompt(self, kill_switch: Path) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("KILL_SWITCH present")
        dialog.grab_set()

        text = (
            "紧急停止开关(KILL_SWITCH)仍在，系统按安全规则拒绝启动。\n"
            "如果你是故意停机：保持不动即可。\n"
            "如果你要恢复运行：点击 Remove & Start（将删除 KILL_SWITCH 并启动 quotes/alerts）"
        )

        tk.Label(dialog, text=text, justify=tk.LEFT, wraplength=520).pack(anchor="w", padx=10, pady=10)
        tk.Label(dialog, text=f"位置: {kill_switch}", fg="gray").pack(anchor="w", padx=10)

        button_frame = tk.Frame(dialog)
        button_frame.pack(pady=10)

        def do_remove_and_start() -> None:
            dialog.destroy()
            self._run_supervisor_async(["start", "--force-remove-kill-switch"])

        def do_cancel() -> None:
            dialog.destroy()

        tk.Button(button_frame, text="Remove & Start", command=do_remove_and_start).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Cancel", command=do_cancel).pack(side=tk.LEFT, padx=5)

    def _run_supervisor_async(self, commands: Sequence[str]) -> None:
        threading.Thread(target=self._run_supervisor, args=(commands,), daemon=True).start()

    def _run_supervisor(self, commands: Sequence[str]) -> None:
        with self._lock:
            proc = run_supervisor_command(commands)
        result = RunResult(
            command=[sys.executable, str(SUPERVISOR_SCRIPT), *commands],
            cwd=ROOT,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
        self._verify_output_queue.put(result.format_lines())
        if proc.returncode != 0:
            self._enqueue_ui(lambda: messagebox.showerror("Supervisor", proc.stderr or proc.stdout))
        else:
            self._enqueue_ui(lambda: messagebox.showinfo("Supervisor", proc.stdout or "done"))
        self._log_run(result.format_lines())

    def _run_tool(self, script_name: str) -> None:
        def runner() -> None:
            result = run_verify_script(script_name)
            self._verify_output_queue.put(result.format_lines())
            if result.returncode != 0:
                self._enqueue_ui(
                    lambda: messagebox.showerror(
                        "Verify", f"{script_name} failed with code {result.returncode}"
                    )
                )
        threading.Thread(target=runner, daemon=True).start()

    def _handle_start_training(self) -> None:
        try:
            max_runtime = int(self.training_runtime_var.get())
        except Exception:
            max_runtime = 60
        if max_runtime <= 0:
            max_runtime = 60
        retention = self._parse_retention_settings()
        self.training_status_var.set(f"Status: running (max {max_runtime}s)")
        threading.Thread(
            target=self._run_training,
            args=(max_runtime, retention, False),
            daemon=True,
        ).start()

    def _handle_start_nightly(self) -> None:
        retention = self._parse_retention_settings()
        self.training_status_var.set("Status: running nightly preset (max 28800s)")
        threading.Thread(
            target=self._run_training,
            args=(28800, retention, True),
            daemon=True,
        ).start()

    def _apply_cadence_preset_fields(self, preset_key: str) -> None:
        preset = CADENCE_PRESETS.get(preset_key)
        if not preset:
            return
        self.service_episode_seconds_var.set(str(preset.get("episode_seconds", "")))
        self.service_max_hour_var.set(str(preset.get("max_episodes_per_hour", "")))
        self.service_max_day_var.set(str(preset.get("max_episodes_per_day", "")))
        self.service_cooldown_var.set(str(preset.get("cooldown_seconds_between_episodes", "")))
        self.service_max_steps_var.set(str(preset.get("max_steps", "")))
        self.service_max_trades_var.set(str(preset.get("max_trades", "")))
        self.service_max_events_var.set(str(preset.get("max_events_per_hour", "")))
        self.service_max_disk_var.set(str(preset.get("max_disk_mb", "")))
        self.service_max_runtime_day_var.set(str(preset.get("max_runtime_per_day", "")))

    def _handle_start_service(self) -> None:
        def _parse_int(var: tk.StringVar, default: int) -> int:
            try:
                return max(int(var.get()), 0)
            except Exception:
                return default

        retention = self._parse_retention_settings()
        episode_seconds = _parse_int(self.service_episode_seconds_var, 300)
        max_hour = _parse_int(self.service_max_hour_var, 12)
        max_day = _parse_int(self.service_max_day_var, 200)
        cooldown = _parse_int(self.service_cooldown_var, 10)
        max_steps = _parse_int(self.service_max_steps_var, 5000)
        max_trades = _parse_int(self.service_max_trades_var, 500)
        max_events = _parse_int(self.service_max_events_var, 300)
        max_disk = _parse_int(self.service_max_disk_var, 5000)
        max_runtime_day = _parse_int(self.service_max_runtime_day_var, 28800)
        preset_key = CADENCE_LABELS.get(self.service_cadence_preset_var.get(), "micro")

        def runner() -> None:
            cmd = [
                sys.executable,
                str(TRAIN_SERVICE_SCRIPT),
                "--cadence-preset",
                preset_key,
                "--episode-seconds",
                str(episode_seconds),
                "--max-episodes-per-hour",
                str(max_hour),
                "--max-episodes-per-day",
                str(max_day),
                "--cooldown-seconds-between-episodes",
                str(cooldown),
                "--max-steps",
                str(max_steps),
                "--max-trades",
                str(max_trades),
                "--max-events-per-hour",
                str(max_events),
                "--max-disk-mb",
                str(max_disk),
                "--max-runtime-per-day",
                str(max_runtime_day),
                "--retain-days",
                str(retention.get("retain_days", 7)),
                "--retain-latest-n",
                str(retention.get("retain_latest_n", 50)),
                "--max-total-train-runs-mb",
                str(retention.get("max_total_train_runs_mb", 5000)),
            ]
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_utf8_env(),
            )
            self._training_output_queue.put(f"Started 24/7 service (pid {proc.pid})")

        threading.Thread(target=runner, daemon=True).start()

    def _handle_stop_service(self) -> None:
        try:
            SERVICE_KILL_SWITCH.parent.mkdir(parents=True, exist_ok=True)
            SERVICE_KILL_SWITCH.write_text("STOP", encoding="utf-8")
            self._training_output_queue.put("Kill switch written for train_service")
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Service", f"Failed to write kill switch: {exc}")

    def _run_throughput_diagnose(self) -> None:
        def runner() -> None:
            cmd = [sys.executable, str(THROUGHPUT_DIAG_SCRIPT)]
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(),
            )
            output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            THROUGHPUT_DIAG_REPORT.parent.mkdir(parents=True, exist_ok=True)
            try:
                THROUGHPUT_DIAG_REPORT.write_text(output, encoding="utf-8")
            except Exception:
                pass

            def updater() -> None:
                self._update_throughput_output(output or "(empty output)")
                self._refresh_throughput_panel()
                if proc.returncode != 0:
                    messagebox.showerror("Throughput Diagnose", output or "diagnose failed")

            self._enqueue_ui(updater)

        threading.Thread(target=runner, daemon=True).start()

    def _update_throughput_output(self, content: str) -> None:
        if not hasattr(self, "progress_throughput_output"):
            return
        self.progress_throughput_output.configure(state=tk.NORMAL)
        self.progress_throughput_output.delete("1.0", tk.END)
        self.progress_throughput_output.insert(tk.END, content)
        self.progress_throughput_output.configure(state=tk.DISABLED)

    def _open_throughput_report(self) -> None:
        if not THROUGHPUT_DIAG_REPORT.exists():
            messagebox.showinfo("Throughput Diagnose", "No diagnose report found")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(THROUGHPUT_DIAG_REPORT))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(THROUGHPUT_DIAG_REPORT)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Throughput Diagnose", f"Failed to open report: {exc}")

    def _handle_show_rolling_summary(self) -> None:
        def loader() -> None:
            if SERVICE_ROLLING_SUMMARY.exists():
                try:
                    content = SERVICE_ROLLING_SUMMARY.read_text(encoding="utf-8")
                except Exception as exc:  # pragma: no cover - UI feedback
                    content = f"Failed to read rolling summary: {exc}"
            else:
                run_dir, summary_path = latest_training_summary()
                if summary_path and summary_path.exists():
                    content = summary_path.read_text(encoding="utf-8")
                else:
                    content = "No rolling summary found"
                if run_dir:
                    self._latest_training_run_dir = run_dir
                if summary_path:
                    self._latest_training_summary_path = summary_path
            self._enqueue_ui(lambda: self._update_training_summary_text(content))
            self._training_output_queue.put("Loaded rolling summary into preview")

        threading.Thread(target=loader, daemon=True).start()

    def _handle_open_latest_service_run(self) -> None:
        state = load_service_state()
        run_dir = Path(str(state.get("last_run_dir"))) if state.get("last_run_dir") else None
        if not run_dir:
            messagebox.showinfo("Training", "No service runs found")
            return
        self._latest_training_run_dir = run_dir
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(run_dir))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(run_dir)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Training", f"Failed to open folder: {exc}")

    def _handle_open_latest_service_summary(self) -> None:
        state = load_service_state()
        summary_path = (
            Path(str(state.get("last_summary_path"))) if state.get("last_summary_path") else None
        )
        if not summary_path or not summary_path.exists():
            messagebox.showinfo("Training", "No service summary found")
            return
        self._latest_training_summary_path = summary_path
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(summary_path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(summary_path)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Training", f"Failed to open summary: {exc}")

    def _parse_retention_settings(self) -> dict[str, int]:
        def _parse_int(var: tk.StringVar, default: int) -> int:
            try:
                value = int(var.get())
                return max(value, 0)
            except Exception:
                return default

        return {
            "retain_days": _parse_int(self.retain_days_var, 7),
            "retain_latest_n": _parse_int(self.retain_latest_n_var, 50),
            "max_total_train_runs_mb": _parse_int(self.retain_total_mb_var, 5000),
        }

    def _run_training(self, max_runtime: int, retention: dict[str, int], nightly: bool) -> None:
        result, markers = run_training_daemon(
            max_runtime,
            retain_days=retention.get("retain_days"),
            retain_latest_n=retention.get("retain_latest_n"),
            max_total_train_runs_mb=retention.get("max_total_train_runs_mb"),
            nightly=nightly,
        )
        markers_with_rc = dict(markers)
        markers_with_rc["RETURN_CODE"] = str(result.returncode)
        lines = [result.format_lines()]
        if markers:
            marker_line = ", ".join(f"{k}={v}" for k, v in markers.items())
            lines.append(f"Markers: {marker_line}")
        else:
            lines.append("Markers: (none detected)")
        log_text = "\n".join(lines)
        self._training_output_queue.put(log_text)
        self._append_training_markers(markers_with_rc)
        self._log_run(log_text)
        self._enqueue_ui(self._refresh_wakeup_dashboard)

    def _append_training_markers(self, markers: dict[str, str]) -> None:
        def updater() -> None:
            run_dir_text = markers.get("RUN_DIR") if markers else None
            summary_text = markers.get("SUMMARY_PATH") if markers else None
            stop_reason = markers.get("STOP_REASON") if markers else None
            if run_dir_text:
                self._latest_training_run_dir = Path(run_dir_text)
                self.training_run_dir_var.set(f"RUN_DIR: {run_dir_text}")
            if summary_text:
                self._latest_training_summary_path = Path(summary_text)
                self.training_summary_path_var.set(f"SUMMARY_PATH: {summary_text}")
            return_code = markers.get("RETURN_CODE") if markers else None
            if stop_reason:
                self.training_status_var.set(
                    f"Status: exit {stop_reason} (code {return_code or ''})"
                )
            else:
                finished_text = "Status: finished"
                if return_code is not None:
                    finished_text += f" (code {return_code})"
                self.training_status_var.set(finished_text)

        self._enqueue_ui(updater)

    def _handle_show_latest_training_summary(self) -> None:
        def loader() -> None:
            run_dir, summary_path = latest_training_summary()
            if summary_path is None:
                self._training_output_queue.put("No training summary found.")
                self._enqueue_ui(
                    lambda: self._update_training_summary_text("(no summary files found)")
                )
                return
            self._latest_training_run_dir = run_dir
            self._latest_training_summary_path = summary_path
            try:
                content = summary_path.read_text(encoding="utf-8")
            except Exception as exc:  # pragma: no cover - UI feedback
                content = f"Failed to read {summary_path}: {exc}"
            self._enqueue_ui(lambda: self._update_training_summary_text(content))
            marker_info = f"Latest summary: {summary_path}"
            if run_dir:
                marker_info += f" (run dir: {run_dir})"
            self._training_output_queue.put(marker_info)

        threading.Thread(target=loader, daemon=True).start()

    def _update_training_summary_text(self, content: str) -> None:
        self.training_summary_text.configure(state=tk.NORMAL)
        self.training_summary_text.delete("1.0", tk.END)
        self.training_summary_text.insert(tk.END, content)
        self.training_summary_text.configure(state=tk.DISABLED)

    def _update_wakeup_preview(self, content: str) -> None:
        self.wakeup_summary_preview.configure(state=tk.NORMAL)
        self.wakeup_summary_preview.delete("1.0", tk.END)
        self.wakeup_summary_preview.insert(tk.END, content)
        self.wakeup_summary_preview.configure(state=tk.DISABLED)

    def _handle_tail_events(self) -> None:
        path = latest_events_file()
        if not path:
            message = "(no events file found)"
        else:
            message = f"Recent events tail ({path}):\n{read_text_tail(path, lines=30)}"
        self.training_output.configure(state=tk.NORMAL)
        self.training_output.insert(tk.END, message + "\n\n")
        self.training_output.see(tk.END)
        self.training_output.configure(state=tk.DISABLED)

    def _handle_open_latest_run_folder(self) -> None:
        run_dir, summary_path = latest_training_summary()
        if run_dir is None:
            messagebox.showinfo("Training", "No training runs found")
            return
        self._latest_training_run_dir = run_dir
        self._latest_training_summary_path = summary_path
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(run_dir))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(run_dir)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Training", f"Failed to open folder: {exc}")

    def _open_baseline_guide(self) -> None:
        output_path = BASELINE_GUIDE_PATH
        cmd = [sys.executable, str(BASELINE_GUIDE_SCRIPT), "--output", str(output_path)]
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
        )
        if proc.returncode != 0:
            messagebox.showerror(
                "Baseline Guide",
                f"baseline_fix_guide.py failed:\n{proc.stderr or proc.stdout}",
            )
            return
        if not output_path.exists():
            messagebox.showinfo("Baseline Guide", "Guide file not found.")
            return
        try:
            content = output_path.read_text(encoding="utf-8")
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Baseline Guide", f"Failed to read guide: {exc}")
            return
        dialog = tk.Toplevel(self)
        dialog.title("Baseline Fix Guide")
        dialog.geometry("900x600")
        text = ScrolledText(dialog, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, content)
        text.configure(state=tk.DISABLED)

    def _refresh_wakeup_dashboard(self) -> None:
        runs_root = LOGS_DIR / "train_runs"
        if not find_latest_run_dir or not find_latest_summary_md or not parse_summary_key_fields:
            self.wakeup_warning_var.set("Wake-up helper未加载")
            return
        latest_run_only = find_latest_run_dir(runs_root)
        run_dir, summary_path = find_latest_summary_md(runs_root)

        warning = ""
        if not runs_root.exists():
            warning = "尚未生成训练记录"
        elif summary_path is None:
            warning = "本次 run 尚未写 summary（可能仍在运行）"
        self._latest_wakeup_run_dir = run_dir or latest_run_only
        self._latest_wakeup_summary_path = summary_path

        if summary_path and summary_path.exists():
            fields = parse_summary_key_fields(summary_path)
            preview = fields.raw_preview
            self.wakeup_stop_reason_var.set(f"stop_reason: {fields.stop_reason}")
            self.wakeup_net_change_var.set(f"net_change: {fields.net_change}")
            self.wakeup_max_drawdown_var.set(f"max_drawdown: {fields.max_drawdown}")
            self.wakeup_trades_var.set(f"trades_count: {fields.trades_count}")
            reject_text = ", ".join(fields.reject_reasons_top3)
            self.wakeup_rejects_var.set(f"reject_reasons_top3: {reject_text}")
            if fields.warning:
                warning = fields.warning
        else:
            preview = warning or ""
            self.wakeup_stop_reason_var.set(f"stop_reason: {MISSING_FIELD_TEXT}")
            self.wakeup_net_change_var.set(f"net_change: {MISSING_FIELD_TEXT}")
            self.wakeup_max_drawdown_var.set(f"max_drawdown: {MISSING_FIELD_TEXT}")
            self.wakeup_trades_var.set(f"trades_count: {MISSING_FIELD_TEXT}")
            self.wakeup_rejects_var.set(f"reject_reasons_top3: {MISSING_FIELD_TEXT}")

        run_dir_display = str(self._latest_wakeup_run_dir) if self._latest_wakeup_run_dir else "(none)"
        summary_display = str(summary_path) if summary_path else "(none)"
        self.wakeup_run_dir_var.set(f"latest_run_dir: {run_dir_display}")
        self.wakeup_summary_path_var.set(f"summary_path: {summary_display}")
        self.wakeup_warning_var.set(warning)
        self._update_wakeup_preview(preview)

    def _handle_open_latest_wakeup_run(self) -> None:
        if not self._latest_wakeup_run_dir:
            self._refresh_wakeup_dashboard()
        run_dir = self._latest_wakeup_run_dir
        if run_dir is None:
            messagebox.showinfo("Wake-up Dashboard", "No training runs found")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(run_dir))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(run_dir)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Wake-up Dashboard", f"Failed to open folder: {exc}")

    def _handle_open_latest_wakeup_summary(self) -> None:
        if not self._latest_wakeup_summary_path:
            self._refresh_wakeup_dashboard()
        summary_path = self._latest_wakeup_summary_path
        if summary_path is None:
            messagebox.showinfo("Wake-up Dashboard", "No summary found")
            return
        if hasattr(os, "startfile"):
            try:
                os.startfile(str(summary_path))  # type: ignore[attr-defined]
                return
            except Exception:
                pass
        try:
            content = summary_path.read_text(encoding="utf-8")
            self._update_wakeup_preview(content)
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Wake-up Dashboard", f"Failed to open summary: {exc}")

    def _build_qa_panel(self) -> None:
        panel = tk.LabelFrame(self.qa_tab, text="AI Q&A", padx=5, pady=5)
        panel.pack(fill=tk.BOTH, padx=5, pady=5, expand=True)

        question_frame = tk.Frame(panel)
        question_frame.pack(fill=tk.X, pady=2)
        tk.Label(question_frame, text="Question:").pack(side=tk.LEFT)
        self.question_var = tk.StringVar()
        tk.Entry(question_frame, textvariable=self.question_var, width=80).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        tk.Button(question_frame, text="Generate Q&A Packet", command=self._handle_generate_packet).pack(side=tk.LEFT, padx=5)

        copy_frame = tk.Frame(panel)
        copy_frame.pack(fill=tk.X, pady=2)
        self.packet_path_var = tk.StringVar(value="Last packet: (none)")
        tk.Label(copy_frame, textvariable=self.packet_path_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(copy_frame, text="Copy Packet to Clipboard", command=self._copy_packet).pack(side=tk.LEFT, padx=5)
        tk.Button(copy_frame, text="Open output folder", command=self._open_output_folder).pack(side=tk.LEFT, padx=5)

        instruction = tk.Label(panel, text="Next: paste the AI packet into ChatGPT, then paste the answer below", fg="gray")
        instruction.pack(anchor="w")

        answer_frame = tk.Frame(panel)
        answer_frame.pack(fill=tk.X, pady=2)
        tk.Label(answer_frame, text="Answer (paste from ChatGPT):").pack(anchor="w")
        self.answer_text = tk.Text(panel, height=8, wrap=tk.WORD)
        self.answer_text.pack(fill=tk.BOTH, padx=2, pady=2)

        strict_frame = tk.Frame(panel)
        strict_frame.pack(fill=tk.X, pady=2)
        self.strict_var = tk.BooleanVar(value=False)
        tk.Checkbutton(strict_frame, text="Strict mode (reject trade advice)", variable=self.strict_var).pack(side=tk.LEFT)
        tk.Button(strict_frame, text="Import Answer", command=self._handle_import_answer).pack(side=tk.LEFT, padx=5)

        self.answer_status_var = tk.StringVar(value="Last answer: (none)")
        tk.Label(panel, textvariable=self.answer_status_var, anchor="w").pack(fill=tk.X)

        self.qa_log = ScrolledText(panel, height=10, wrap=tk.WORD)
        self.qa_log.pack(fill=tk.BOTH, padx=2, pady=2, expand=True)

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.qa_log.insert(tk.END, f"[{timestamp}] {message}\n")
        self.qa_log.see(tk.END)

    def _log_run(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.run_log.configure(state=tk.NORMAL)
        self.run_log.insert(tk.END, f"[{timestamp}] {message}\n\n")
        self.run_log.see(tk.END)
        self.run_log.configure(state=tk.DISABLED)

    def _enqueue_ui(self, func) -> None:
        self.after(0, func)

    def _refresh_service_state(self) -> None:
        state = load_service_state()
        running = _service_running(state)
        status_text = "Service: running" if running else "Service: stopped"
        if state.get("stop_reason"):
            status_text += f" ({state.get('stop_reason')})"
        self.service_status_var.set(status_text)

        run_dir_text = state.get("last_run_dir") or "(none)"
        summary_text = state.get("last_summary_path") or "(none)"
        self.service_run_dir_var.set(f"Last run: {run_dir_text}")
        self.service_summary_var.set(f"Last summary: {summary_text}")

        if state.get("last_run_dir"):
            self._latest_training_run_dir = Path(str(state.get("last_run_dir")))
        if state.get("last_summary_path"):
            self._latest_training_summary_path = Path(str(state.get("last_summary_path")))

    def _refresh_proof_lamps(self) -> None:
        baseline_info = probe_baseline()
        baseline_status = baseline_info.get("status") or "UNAVAILABLE"
        baseline = baseline_info.get("baseline") or "unavailable"
        baseline_details = baseline_info.get("details") or "unknown"
        baseline_reason_map = {
            "no_origin": "No origin remote",
            "no_main_ref": "No main/master ref",
            "shallow_repo": "Shallow repository",
            "git_error": "Git error",
        }
        if baseline_status == "AVAILABLE":
            lamp_text = "AVAILABLE"
            lamp_color = "#16a34a"
            reason = f"Using {baseline}"
        else:
            lamp_text = "UNAVAILABLE"
            lamp_color = "#f97316"
            reason = baseline_reason_map.get(baseline_details, baseline_details)
        if self.proof_baseline_lamp:
            self.proof_baseline_lamp.configure(text=lamp_text, bg=lamp_color)
        self.proof_baseline_status_var.set(f"Baseline: {lamp_text}")
        self.proof_baseline_detail_var.set(f"Reason: {reason}")

        state = load_service_state()
        running = _service_running(state)
        hb_age: float | None = None
        hb_value = state.get("last_heartbeat_ts")
        if hb_value:
            try:
                ts = ensure_aware_utc(parse_iso_timestamp(hb_value))
                if ts is not None:
                    hb_age = max(0.0, (utc_now() - ts).total_seconds())
            except Exception:
                hb_age = None
        service_text = "RUNNING" if running else "STOPPED"
        service_color = "#16a34a" if running else "#b91c1c"
        if self.proof_service_lamp:
            self.proof_service_lamp.configure(text=service_text, bg=service_color)
        self.proof_service_status_var.set(f"Training Service: {service_text}")
        self.proof_service_detail_var.set(f"Heartbeat age: {_format_age(hb_age)}")

        judge_age: float | None = None
        if PROGRESS_JUDGE_LATEST_PATH.exists():
            try:
                judge_age = max(0.0, time.time() - PROGRESS_JUDGE_LATEST_PATH.stat().st_mtime)
            except Exception:
                judge_age = None
        judge_updated = judge_age is not None and judge_age <= JUDGE_STALE_SECONDS
        judge_text = "UPDATED" if judge_updated else "STALE"
        judge_color = "#16a34a" if judge_updated else "#b91c1c"
        if self.proof_judge_lamp:
            self.proof_judge_lamp.configure(text=judge_text, bg=judge_color)
        self.proof_judge_status_var.set(f"Judge Freshness: {judge_text}")
        if judge_age is None:
            self.proof_judge_detail_var.set("Last updated: missing")
        else:
            self.proof_judge_detail_var.set(f"Last updated: {_format_age(judge_age)} ago")

        smoke_payload = _load_ui_smoke_latest(UI_SMOKE_LATEST_PATH)
        smoke_status = str(smoke_payload.get("status", "")).upper() if smoke_payload else "MISSING"
        smoke_reason = str(smoke_payload.get("reason", "missing")) if smoke_payload else "missing"
        smoke_ts = ensure_aware_utc(parse_iso_timestamp(smoke_payload.get("ts_utc")))
        smoke_age = max(0.0, (utc_now() - smoke_ts).total_seconds()) if smoke_ts else None
        smoke_colors = {
            "PASS": "#16a34a",
            "SKIP": "#f97316",
            "FAIL": "#b91c1c",
            "MISSING": "#555",
        }
        smoke_color = smoke_colors.get(smoke_status, "#555")
        if self.proof_ui_smoke_lamp:
            self.proof_ui_smoke_lamp.configure(text=smoke_status, bg=smoke_color)
        self.proof_ui_smoke_status_var.set(f"UI Smoke: {smoke_status}")
        if smoke_age is None:
            self.proof_ui_smoke_detail_var.set(f"Reason: {smoke_reason}")
        else:
            self.proof_ui_smoke_detail_var.set(f"Last updated: {_format_age(smoke_age)} ago | {smoke_reason}")

    def _refresh_training_hud(self) -> None:
        if not compute_training_hud:
            return
        snapshot = compute_training_hud()
        lamp_colors = {
            "RUNNING": "#228B22",
            "OBSERVE": "#DAA520",
            "SAFE": "#FF8C00",
            "STOPPED": "#8B0000",
        }
        color = lamp_colors.get(snapshot.mode, "#555")
        self.hud_mode_lamp.configure(text=snapshot.mode, bg=color)
        self.hud_mode_detail_var.set(f"Status: {snapshot.mode_detail}")
        kill_paths = [Path(p) for p in snapshot.kill_switch_paths]
        detected = [str(p) for p in kill_paths if p.exists()]
        kill_paths_text = ", ".join(detected) if detected else "(none)"
        self.hud_kill_switch_var.set(f"Kill switch: {snapshot.kill_switch}")
        self.hud_kill_switch_detail_var.set(f"Detected paths: {kill_paths_text}")
        last_event_ts, last_event_source = _last_kill_switch_event(LOGS_DIR)
        last_trip_ts = None
        last_trip_source = ""
        if last_event_ts:
            last_trip_ts = last_event_ts
            last_trip_source = f"events:{last_event_source}" if last_event_source else "events"
        if last_trip_ts is None and detected:
            mtimes = []
            for path in kill_paths:
                if not path.exists():
                    continue
                try:
                    mtimes.append(path.stat().st_mtime)
                except OSError:
                    continue
            if mtimes:
                last_trip_ts = datetime.fromtimestamp(max(mtimes), tz=timezone.utc)
                last_trip_source = "mtime"
        if last_trip_ts:
            self.hud_kill_switch_trip_var.set(
                f"Last trip: {last_trip_ts.isoformat()} (source: {last_trip_source or 'unknown'})"
            )
        else:
            self.hud_kill_switch_trip_var.set("Last trip: unknown (source: none)")
        data_text = f"Data health: {snapshot.data_health}"
        if snapshot.data_health_detail:
            data_text += f" ({snapshot.data_health_detail})"
        self.hud_data_health_var.set(data_text)
        if snapshot.data_health == "OK":
            health_color = "#228B22"
        elif snapshot.data_health == "WARN":
            health_color = "#DAA520"
        elif snapshot.data_health == "UNKNOWN":
            health_color = "#708090"
        else:
            health_color = "#8B0000"
        self.hud_data_health_label.configure(bg=health_color, fg="white")
        self.hud_stage_var.set(f"Stage: {snapshot.stage}")
        self.hud_run_id_var.set(f"Run: {snapshot.run_id}")
        self.hud_elapsed_var.set(f"Elapsed: {snapshot.elapsed}")
        self.hud_next_iter_var.set(f"Next iteration: {snapshot.next_iteration}")
        budgets = snapshot.budgets
        self.hud_budget_iter_var.set(
            f"Episodes/day: {budgets.get('episodes_completed', '?')}/{budgets.get('max_per_day', '?')}"
        )
        self.hud_budget_hour_var.set(
            f"Episodes/hour: {budgets.get('episodes_completed', '?')}/{budgets.get('max_per_hour', '?')}"
        )
        self.hud_budget_disk_var.set(f"Disk budget MB: {budgets.get('disk_budget_mb', '?')}")
        self.hud_max_dd_var.set(f"Max drawdown: {snapshot.risk.get('max_drawdown', MISSING_FIELD_TEXT)}")
        self.hud_turnover_var.set(f"Turnover: {snapshot.risk.get('turnover', MISSING_FIELD_TEXT)}")
        self.hud_rejects_var.set(f"Rejects: {snapshot.risk.get('reject_count', MISSING_FIELD_TEXT)} | {snapshot.risk.get('rejects', '')}")
        self.hud_gates_var.set(f"Gates triggered: {snapshot.risk.get('gates_triggered', MISSING_FIELD_TEXT)}")
        self.hud_equity_var.set(f"Equity delta: {snapshot.equity}")
        if snapshot.run_dir:
            self._latest_training_run_dir = snapshot.run_dir
        if snapshot.summary_path:
            self._latest_training_summary_path = snapshot.summary_path
            if parse_summary_key_fields and snapshot.summary_path != self._hud_last_summary_rendered:
                summary_fields = parse_summary_key_fields(snapshot.summary_path)
                self._update_training_summary_text(summary_fields.raw_preview)
                self._hud_last_summary_rendered = snapshot.summary_path

    def _refresh(self) -> None:
        self._load_dashboard()
        self._refresh_wakeup_dashboard()
        self._refresh_service_state()
        self._refresh_proof_lamps()
        self._refresh_action_center_report()
        self._refresh_training_hud()
        self._refresh_truthful_progress()
        self._refresh_xp_snapshot()
        self._refresh_policy_history()
        self._refresh_replay_panel()
        self._maybe_auto_refresh_progress()
        self._refresh_throughput_panel()

    def _maybe_auto_refresh_progress(self) -> None:
        if not self.progress_auto_refresh_var.get():
            return
        try:
            interval = int(self.progress_refresh_interval_var.get())
        except Exception:
            interval = 15
        now = time.time()
        if self._progress_last_refresh_ts and (now - self._progress_last_refresh_ts) < max(interval, 1):
            return
        index_mtime = None
        if self.progress_index_path.exists():
            try:
                index_mtime = self.progress_index_path.stat().st_mtime
            except Exception:
                index_mtime = None
        latest_mtime = None
        for path in [
            PROGRESS_JUDGE_LATEST_PATH,
            LATEST_POLICY_HISTORY_PATH,
            LATEST_PROMOTION_DECISION_PATH,
            LATEST_TOURNAMENT_PATH,
            XP_SNAPSHOT_LATEST_PATH,
        ]:
            if not path.exists():
                continue
            try:
                latest_mtime = max(latest_mtime or 0.0, path.stat().st_mtime)
            except Exception:
                continue

        should_refresh_index = index_mtime is None or index_mtime != self._progress_last_index_mtime
        should_refresh_latest = latest_mtime is None or latest_mtime != self._progress_last_latest_mtime
        if should_refresh_index:
            self._load_progress_index()
        if should_refresh_latest:
            self._refresh_truthful_progress()
            self._refresh_xp_snapshot()
            self._refresh_engine_status()
            self._refresh_policy_history()
        self._progress_last_index_mtime = index_mtime
        self._progress_last_latest_mtime = latest_mtime
        self._progress_last_refresh_ts = now
        refresh_ts = utc_now().isoformat()
        self.progress_last_refresh_var.set(f"Last refresh: {refresh_ts}")

    def _refresh_throughput_panel(self) -> None:
        now = utc_now()
        last_run_ts = None
        if self.progress_entries:
            first = self.progress_entries[0] if isinstance(self.progress_entries[0], dict) else {}
            last_run_ts = ensure_aware_utc(parse_iso_timestamp(first.get("mtime")))
        runs_last_hour = 0
        for entry in self.progress_entries:
            if not isinstance(entry, dict):
                continue
            parsed = ensure_aware_utc(parse_iso_timestamp(entry.get("mtime")))
            if parsed and (now - parsed) <= timedelta(hours=1):
                runs_last_hour += 1

        state = load_service_state()
        config = state.get("config") if isinstance(state.get("config"), dict) else {}
        target_per_hour = state.get("target_runs_per_hour") or config.get("max_episodes_per_hour") or "-"
        self.progress_run_rate_var.set(f"Run-rate: {runs_last_hour}/hr (target {target_per_hour})")

        age_text = "unknown"
        age_seconds = None
        if last_run_ts:
            age_seconds = max(0.0, (now - last_run_ts).total_seconds())
            age_text = _format_age(age_seconds)
        self.progress_run_stale_var.set(
            f"Last run: {last_run_ts.isoformat() if last_run_ts else 'missing'} | age {age_text}"
        )

        expected_eta = None
        next_eta = state.get("next_run_eta_s") if isinstance(state, dict) else None
        if isinstance(next_eta, (int, float)) and next_eta >= 0:
            expected_eta = float(next_eta) + 60.0
        last_duration = state.get("last_run_duration_s") if isinstance(state, dict) else None
        if expected_eta is None and isinstance(last_duration, (int, float)) and last_duration > 0:
            expected_eta = float(last_duration) + 60.0
        stale = False
        if age_seconds is None:
            stale = True
        elif expected_eta is not None and age_seconds > expected_eta:
            stale = True

        lamp_color = "#b91c1c" if stale else "#16a34a"
        lamp_text = "STALE" if stale else "ACTIVE"
        if hasattr(self, "progress_throughput_lamp") and self.progress_throughput_lamp:
            self.progress_throughput_lamp.configure(text=lamp_text, bg=lamp_color)

        summary_line = ""
        if THROUGHPUT_DIAG_REPORT.exists():
            try:
                for line in THROUGHPUT_DIAG_REPORT.read_text(encoding="utf-8").splitlines():
                    if line.startswith("THROUGHPUT_DIAG_SUMMARY"):
                        summary_line = line
                        break
            except Exception:
                summary_line = ""
        self.progress_throughput_diag_var.set(
            summary_line or "Throughput diagnosis: (run diagnose for details)"
        )

    def _sparkline_text(self, values: List[float]) -> str:
        if not values:
            return "(no equity points)"
        chars = "▁▂▃▄▅▆▇█"
        lo, hi = min(values), max(values)
        if hi == lo:
            return chars[0] * min(len(values), 60)
        step = max(1, len(values) // 60)
        sampled = values[::step][:60]
        result = ""
        for v in sampled:
            idx = int((v - lo) / (hi - lo) * (len(chars) - 1))
            result += chars[idx]
        return result

    def _draw_equity_canvas(
        self,
        series_list: List[List[float]],
        stats: dict[str, object] | None = None,
        drawdown_points: List[float] | None = None,
        label_values: List[float] | None = None,
    ) -> None:
        if not hasattr(self, "progress_equity_canvas"):
            return
        canvas = self.progress_equity_canvas
        canvas.delete("all")
        if not series_list or not any(series_list):
            canvas.create_text(10, 20, anchor="w", text="No equity curve available")
            return
        width = int(canvas.winfo_width() or 320)
        height = int(canvas.winfo_height() or 120)
        colors = ["#2563eb", "#16a34a", "#f97316", "#7c3aed", "#dc2626"]
        pad = 10
        for idx, values in enumerate(series_list):
            if not values:
                continue
            if not compute_polyline:
                return
            points = compute_polyline(values, width, height, pad)
            for j in range(1, len(points)):
                x0, y0 = points[j - 1]
                x1, y1 = points[j]
                canvas.create_line(x0, y0, x1, y1, fill=colors[idx % len(colors)], width=2)
            if idx == 0:
                if label_values:
                    label_start = label_values[0]
                    label_end = label_values[-1]
                    canvas.create_text(
                        points[0][0] + 4,
                        points[0][1] + 8,
                        anchor="w",
                        text=f"Start {label_start:.2f}",
                    )
                    canvas.create_text(
                        points[-1][0] - 4,
                        points[-1][1] - 8,
                        anchor="e",
                        text=f"End {label_end:.2f}",
                    )
                if drawdown_points:
                    clean_drawdowns = [dd for dd in drawdown_points if isinstance(dd, (int, float))]
                    if clean_drawdowns:
                        max_dd = max(clean_drawdowns)
                        idx_dd = drawdown_points.index(max_dd) if max_dd in drawdown_points else None
                        if idx_dd is not None and idx_dd < len(points):
                            x, y = points[idx_dd]
                            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, outline="#dc2626", width=2)
                            canvas.create_text(x + 6, y - 6, anchor="w", text=f"Max DD {max_dd:.2f}%")

    def _collect_recent_equity_series(self, limit: int, window_hours: int | None = None) -> List[List[float]]:
        series: List[List[float]] = []
        now = utc_now()
        for entry in self.progress_entries:
            if len(series) >= limit:
                break
            if not isinstance(entry, dict):
                continue
            if entry.get("run_complete") is False:
                continue
            if window_hours is not None:
                parsed = ensure_aware_utc(parse_iso_timestamp(entry.get("mtime")))
                if not parsed or (now - parsed) > timedelta(hours=window_hours):
                    continue
            points = entry.get("equity_points", [])
            values = [float(p.get("equity", 0.0)) for p in points if isinstance(p, dict)]
            if values:
                series.append(values)
        return series

    def _refresh_progress_diagnosis(self) -> None:
        if not compute_progress_diagnosis:
            self.progress_diag_status_var.set("Diagnosis: unavailable")
            self.progress_diag_summary_var.set("progress_diagnose module not available.")
            return
        diagnosis = compute_progress_diagnosis()
        primary = diagnosis.get("primary_reason", "unknown")
        status = diagnosis.get("status", "WARN")
        summary = diagnosis.get("summary", "")
        ranked = diagnosis.get("reasons_ranked", [])
        ranked_text = ", ".join(ranked) if isinstance(ranked, list) else str(ranked)
        self.progress_diag_status_var.set(f"Diagnosis: {primary} ({status})")
        detail = summary or "No diagnosis summary."
        if ranked_text:
            detail += f" Ranked reasons: {ranked_text}"
        self.progress_diag_summary_var.set(detail)

    def _update_progress_growth_hud(self) -> None:
        runs_total = len(self.progress_entries)
        self.progress_growth_total_var.set(f"Total runs: {runs_total}")
        now = utc_now()
        complete_entries = [
            entry
            for entry in self.progress_entries
            if isinstance(entry, dict) and entry.get("run_complete") is True
        ]
        runs_today = 0
        last_run_time = "unknown"
        last_net_change = "unknown"
        last_max_dd = "unknown"
        last_rejects = "unknown"
        last_gates = "unknown"
        last_cash_delta = "unknown"
        last_missing_reason = ""
        cash_24h_delta = None
        cash_24h_missing = ""
        seven_day_net = 0.0
        seven_day_count = 0
        latest_cash_end = None
        oldest_cash_start = None
        latest_xp_total = None
        for idx, entry in enumerate(self.progress_entries):
            if not isinstance(entry, dict):
                continue
            raw_mtime = entry.get("mtime")
            parsed = ensure_aware_utc(parse_iso_timestamp(raw_mtime))
            if parsed is None:
                if idx == 0:
                    last_run_time = "unknown"
                continue
            if parsed.date() == now.date():
                runs_today += 1
            if idx == 0:
                last_run_time = parsed.isoformat()

        for idx, entry in enumerate(complete_entries):
            raw_mtime = entry.get("mtime")
            parsed = ensure_aware_utc(parse_iso_timestamp(raw_mtime))
            if parsed is None:
                continue
            if (now - parsed).days <= 7:
                summary = entry.get("summary", {}) if isinstance(entry.get("summary", {}), dict) else {}
                net = summary.get("net_change")
                if isinstance(net, (int, float)):
                    seven_day_net += float(net)
                    seven_day_count += 1
            if (now - parsed) <= timedelta(hours=24):
                equity_points = (
                    entry.get("equity_points", [])
                    if isinstance(entry.get("equity_points", []), list)
                    else []
                )
                cash_points = [
                    float(p.get("cash", 0.0))
                    for p in equity_points
                    if isinstance(p, dict) and isinstance(p.get("cash"), (int, float))
                ]
                if cash_points:
                    if latest_cash_end is None:
                        latest_cash_end = cash_points[-1]
                    oldest_cash_start = cash_points[0]
            if idx == 0:
                summary = entry.get("summary", {}) if isinstance(entry.get("summary", {}), dict) else {}
                net = summary.get("net_change")
                if isinstance(net, (int, float)):
                    last_net_change = f"{net:+.2f}"
                else:
                    last_missing_reason = entry.get("missing_reason") or "net_change_missing"
                    last_net_change = f"missing ({last_missing_reason})"
                equity_points = (
                    entry.get("equity_points", [])
                    if isinstance(entry.get("equity_points", []), list)
                    else []
                )
                cash_points = [
                    float(p.get("cash", 0.0))
                    for p in equity_points
                    if isinstance(p, dict) and isinstance(p.get("cash"), (int, float))
                ]
                if cash_points:
                    last_cash_delta = f"{(cash_points[-1] - cash_points[0]):+.2f}"
                else:
                    last_cash_delta = f"missing ({last_missing_reason or 'cash_missing'})"
                max_dd = summary.get("max_drawdown")
                if isinstance(max_dd, (int, float)):
                    last_max_dd = f"{max_dd:.2f}%"
                rejects = summary.get("rejects_count")
                if rejects in (None, ""):
                    rejects = summary.get("reject_count")
                if isinstance(rejects, (int, float)):
                    last_rejects = str(rejects)
                gates = summary.get("gates_triggered")
                if isinstance(gates, (int, float)):
                    last_gates = str(gates)
                elif isinstance(gates, str):
                    last_gates = gates

        if not complete_entries:
            if self.progress_entries:
                first = self.progress_entries[0]
                if isinstance(first, dict):
                    last_missing_reason = first.get("missing_reason") or "no_complete_runs"
            last_net_change = f"missing ({last_missing_reason or 'no_complete_runs'})"
            last_cash_delta = f"missing ({last_missing_reason or 'no_complete_runs'})"

        self.progress_growth_runs_today_var.set(f"Runs today: {runs_today} | Last run: {last_run_time}")
        self.progress_growth_last_net_var.set(f"Last run net: {last_net_change}")
        self.progress_growth_cash_last_var.set(f"Δcash (last run): {last_cash_delta}")
        if latest_cash_end is not None and oldest_cash_start is not None:
            cash_24h_delta = latest_cash_end - oldest_cash_start
        if cash_24h_delta is None:
            cash_24h_missing = last_missing_reason or "cash_missing"
            self.progress_growth_cash_24h_var.set(f"Δcash (24h): missing ({cash_24h_missing})")
        else:
            self.progress_growth_cash_24h_var.set(f"Δcash (24h): {cash_24h_delta:+.2f}")
        if seven_day_count:
            self.progress_growth_seven_day_var.set(f"7-day net: {seven_day_net:+.2f}")
        else:
            reason = last_missing_reason or "insufficient_history"
            self.progress_growth_seven_day_var.set(f"7-day net: missing ({reason})")
        self.progress_growth_max_dd_var.set(f"Max drawdown (last): {last_max_dd}")
        self.progress_growth_rejects_var.set(f"Rejects: {last_rejects}")
        self.progress_growth_gates_var.set(f"Gates triggered: {last_gates}")

        state = load_service_state()
        heartbeat_age = None
        heartbeat = state.get("last_heartbeat_ts") if isinstance(state, dict) else None
        if heartbeat:
            try:
                ts = ensure_aware_utc(parse_iso_timestamp(heartbeat))
                if ts is not None:
                    heartbeat_age = int((utc_now() - ts).total_seconds())
            except Exception:
                heartbeat_age = None
        stop_reason = state.get("stop_reason") if isinstance(state, dict) else None
        if heartbeat_age is not None and heartbeat_age < 180 and not stop_reason:
            service_status = f"RUNNING (heartbeat {heartbeat_age}s)"
        elif state:
            age_text = f"{heartbeat_age}s" if heartbeat_age is not None else "unknown"
            service_status = f"STOPPED (heartbeat {age_text})"
        else:
            service_status = "STOPPED (no state)"
        self.progress_growth_service_var.set(f"Service: {service_status}")

        kill_switch_paths = [SERVICE_KILL_SWITCH, get_kill_switch_path()]
        kill_triggered = [str(path) for path in kill_switch_paths if path.exists()]
        kill_status = "TRIPPED" if kill_triggered else "CLEAR"
        self.progress_growth_kill_var.set(f"Kill switch: {kill_status}")
        snapshot = self._xp_snapshot_cache or load_xp_snapshot_latest(XP_SNAPSHOT_LATEST_PATH)
        if isinstance(snapshot, dict):
            xp_total = snapshot.get("xp_total")
            if isinstance(xp_total, (int, float)):
                latest_xp_total = float(xp_total)

        xp_age = None
        if XP_SNAPSHOT_LATEST_PATH.exists():
            try:
                xp_age = max(0.0, time.time() - XP_SNAPSHOT_LATEST_PATH.stat().st_mtime)
            except Exception:
                xp_age = None
        xp_age_text = _format_age(xp_age) if xp_age is not None else "missing"
        if latest_xp_total is not None:
            self.progress_growth_truth_xp_var.set(
                f"Truthful XP: {latest_xp_total:.1f} | xp age {xp_age_text}"
            )
        else:
            self.progress_growth_truth_xp_var.set(f"Truthful XP: missing | xp age {xp_age_text}")

    def _load_progress_index(self) -> None:
        path = self.progress_index_path
        if not path.exists():
            self.progress_status_var.set(f"progress_index.json missing: {path}")
            self.progress_entries = []
            self._render_progress_entries()
            self._refresh_skill_tree_panel()
            self._refresh_upgrade_log()
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.progress_status_var.set(f"Failed to load progress index: {exc}")
            self.progress_entries = []
            self._render_progress_entries()
            self._refresh_skill_tree_panel()
            self._refresh_upgrade_log()
            return
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        self.progress_entries = entries if isinstance(entries, list) else []
        generated_ts = payload.get("generated_ts") if isinstance(payload, dict) else None
        self.progress_status_var.set(
            f"Progress index runs={len(self.progress_entries)} | generated_at={generated_ts or 'unknown'}"
        )
        latest_run_id = None
        if self.progress_entries:
            first_entry = self.progress_entries[0] if isinstance(self.progress_entries[0], dict) else {}
            latest_run_id = str(first_entry.get("run_id") or "")
        if latest_run_id and latest_run_id != self._progress_last_run_id:
            self._progress_last_run_id = latest_run_id
            self._progress_last_new_run_ts = utc_now()
            self.progress_last_new_run_var.set(
                f"Last new run: {self._progress_last_new_run_ts.isoformat()} (run_id={latest_run_id})"
            )
        elif self._progress_last_new_run_ts:
            self.progress_last_new_run_var.set(
                f"Last new run: {self._progress_last_new_run_ts.isoformat()} (run_id={self._progress_last_run_id})"
            )
        self._render_progress_entries()
        self._refresh_progress_diagnosis()
        self._update_progress_growth_hud()
        self._refresh_skill_tree_panel()
        self._refresh_upgrade_log()

    def _progress_status_label(self, entry: dict[str, object]) -> str:
        status = entry.get("status")
        if entry.get("run_complete") is False:
            return "INCOMPLETE"
        if entry.get("still_writing"):
            return "IN_PROGRESS"
        if entry.get("parse_error"):
            return "PARSE_ERROR"
        if status in ("OK", "MISSING"):
            return str(status)
        has_equity = entry.get("has_equity_curve")
        has_summary = entry.get("has_summary_json")
        has_holdings = entry.get("has_holdings_json")
        if not (has_equity and has_summary and has_holdings):
            return "MISSING_FILES"
        return "OK"

    def _progress_missing_reason(self, entry: dict[str, object]) -> str:
        parts: list[str] = []
        missing = entry.get("missing_reason")
        if isinstance(missing, str) and missing:
            parts.append(missing)
        missing_artifacts = entry.get("missing_artifacts") if isinstance(entry.get("missing_artifacts"), list) else []
        if missing_artifacts:
            parts.append(f"artifacts={','.join(str(item) for item in missing_artifacts)}")
        missing_paths = entry.get("missing_paths") if isinstance(entry.get("missing_paths"), list) else []
        if missing_paths:
            parts.append(f"paths={','.join(str(item) for item in missing_paths)}")
        next_action = entry.get("next_action")
        if isinstance(next_action, str) and next_action:
            parts.append(f"next={next_action}")
        return " | ".join(parts) if parts else "none"

    def _render_progress_entries(self) -> None:
        if not hasattr(self, "progress_tree"):
            return
        tree = self.progress_tree
        for item in tree.get_children():
            tree.delete(item)
        for entry in self.progress_entries:
            summary = entry.get("summary", {}) if isinstance(entry, dict) else {}
            missing_reason = self._progress_missing_reason(entry) if isinstance(entry, dict) else "none"
            raw_net = summary.get("net_change") if isinstance(summary, dict) else None
            raw_stop = summary.get("stop_reason") if isinstance(summary, dict) else None
            if isinstance(raw_net, (int, float)):
                net_change = f"{raw_net:+.2f}"
            else:
                net_change = str(raw_net) if raw_net not in (None, "") else missing_reason
            stop_reason = str(raw_stop) if raw_stop not in (None, "") else missing_reason
            mtime = entry.get("mtime", "")
            status = self._progress_status_label(entry) if isinstance(entry, dict) else "MISSING_FILES"
            tree.insert("", tk.END, values=(entry.get("run_id", "-"), status, net_change, stop_reason, mtime))
        if self.progress_entries:
            tree.selection_set(tree.get_children()[0])
            self._on_progress_select()
        else:
            self._render_progress_detail(None)

    def _handle_generate_progress_index(self) -> None:
        def runner() -> None:
            cmd = [sys.executable, str(PROGRESS_INDEX_SCRIPT)]
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(),
            )
            output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

            def updater() -> None:
                if proc.returncode == 0:
                    self.progress_status_var.set("Progress index refreshed from disk")
                    self._load_progress_index()
                    self._refresh_truthful_progress()
                    self._refresh_friction_status()
                    self._refresh_xp_snapshot()
                    self._refresh_engine_status()
                    self._refresh_policy_history()
                    self._refresh_skill_tree_panel()
                    self._refresh_upgrade_log()
                    self.progress_last_refresh_var.set(f"Last refresh: {utc_now().isoformat()}")
                else:
                    self.progress_status_var.set("Progress index generation failed")
                    messagebox.showerror("Progress", output or "progress_index.py failed")
                self._log_run(output or "progress index run complete")

            self._enqueue_ui(updater)

        threading.Thread(target=runner, daemon=True).start()

    def _handle_refresh_progress_view(self) -> None:
        self.progress_status_var.set("Refreshing progress index view...")
        self._load_progress_index()
        self._refresh_action_center_report()
        self._refresh_truthful_progress()
        self._refresh_friction_status()
        self._refresh_xp_snapshot()
        self._refresh_engine_status()
        self._refresh_policy_history()
        self._refresh_skill_tree_panel()
        self._refresh_upgrade_log()

    def _build_replay_tab(self) -> None:
        status_frame = tk.LabelFrame(self.replay_tab, text="Replay status (SIM-only)", padx=6, pady=6)
        status_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(status_frame, textvariable=self.replay_status_var, anchor="w").pack(anchor="w")
        tk.Label(status_frame, textvariable=self.replay_detail_var, anchor="w").pack(anchor="w")
        tk.Label(status_frame, textvariable=self.replay_latest_path_var, anchor="w", fg="#6b7280").pack(
            anchor="w"
        )

        status_actions = tk.Frame(status_frame)
        status_actions.pack(fill=tk.X, pady=4)
        tk.Button(status_actions, text="Open latest replay file", command=self._open_replay_latest_file).pack(
            side=tk.LEFT, padx=3
        )
        tk.Button(status_actions, text="Copy replay path", command=self._copy_replay_latest_path).pack(
            side=tk.LEFT, padx=3
        )

        filter_frame = tk.LabelFrame(self.replay_tab, text="Filters", padx=6, pady=6)
        filter_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(filter_frame, text="Action").grid(row=0, column=0, sticky="w")
        ttk.OptionMenu(
            filter_frame,
            self.replay_action_filter_var,
            self.replay_action_filter_var.get(),
            "(all)",
            "BUY",
            "SELL",
            "HOLD",
            "REJECT",
            "NOOP",
        ).grid(row=0, column=1, sticky="w", padx=4)
        tk.Label(filter_frame, text="Symbol").grid(row=0, column=2, sticky="w")
        tk.Entry(filter_frame, textvariable=self.replay_symbol_filter_var, width=16).grid(
            row=0, column=3, sticky="w", padx=4
        )
        tk.Checkbutton(
            filter_frame, text="Has reject", variable=self.replay_reject_only_var
        ).grid(row=0, column=4, sticky="w", padx=4)
        tk.Checkbutton(
            filter_frame, text="Guard failed", variable=self.replay_guard_failed_var
        ).grid(row=0, column=5, sticky="w", padx=4)
        tk.Button(filter_frame, text="Apply filters", command=self._apply_replay_filters).grid(
            row=0, column=6, sticky="w", padx=4
        )

        table_frame = tk.LabelFrame(self.replay_tab, text="Decision cards", padx=6, pady=6)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        columns = ("ts_utc", "step_id", "symbol", "action", "accepted", "rejects")
        self.replay_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        for name, width in [
            ("ts_utc", 170),
            ("step_id", 100),
            ("symbol", 120),
            ("action", 90),
            ("accepted", 90),
            ("rejects", 240),
        ]:
            self.replay_tree.heading(name, text=name)
            self.replay_tree.column(name, width=width, anchor="w")
        self.replay_tree.pack(fill=tk.BOTH, expand=True)
        self.replay_tree.bind("<<TreeviewSelect>>", self._on_replay_select)

        details_frame = tk.LabelFrame(self.replay_tab, text="Card details", padx=6, pady=6)
        details_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        tk.Label(details_frame, textvariable=self.replay_selected_detail_var, anchor="w").pack(anchor="w")
        detail_actions = tk.Frame(details_frame)
        detail_actions.pack(fill=tk.X, pady=2)
        tk.Button(detail_actions, text="Copy evidence paths", command=self._copy_replay_evidence).pack(
            side=tk.LEFT, padx=3
        )
        self.replay_detail_text = ScrolledText(details_frame, height=10, wrap=tk.WORD)
        self.replay_detail_text.pack(fill=tk.BOTH, expand=True)
        self.replay_detail_text.configure(state=tk.DISABLED)
        self.progress_last_refresh_var.set(f"Last refresh: {utc_now().isoformat()}")

    def _refresh_action_center_report(self) -> None:
        if not hasattr(self, "action_center_tree"):
            return
        if not ACTION_CENTER_REPORT_PATH.exists():
            self.action_center_status_marker_var.set("ACTION_CENTER_STATUS: ISSUE")
            self.action_center_last_report_var.set("LAST_REPORT_TS_UTC: -")
            self._action_center_report = None
            self._render_action_center_actions([])
            self._refresh_action_center_apply_status()
            self._refresh_action_center_doctor()
            return
        try:
            mtime = ACTION_CENTER_REPORT_PATH.stat().st_mtime
        except Exception:
            mtime = None
        if mtime is not None and mtime == self._action_center_last_mtime and self._action_center_report:
            self._refresh_action_center_apply_status()
            return
        try:
            payload = json.loads(ACTION_CENTER_REPORT_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            self.action_center_status_marker_var.set("ACTION_CENTER_STATUS: ISSUE")
            self.action_center_last_report_var.set("LAST_REPORT_TS_UTC: -")
            self._action_center_report = None
            self._render_action_center_actions([])
            self._refresh_action_center_apply_status()
            self._refresh_action_center_doctor()
            return
        if not isinstance(payload, dict):
            self.action_center_status_marker_var.set("ACTION_CENTER_STATUS: ISSUE")
            self.action_center_last_report_var.set("LAST_REPORT_TS_UTC: -")
            self._action_center_report = None
            self._render_action_center_actions([])
            self._refresh_action_center_apply_status()
            self._refresh_action_center_doctor()
            return
        self._action_center_report = payload
        self._action_center_last_mtime = mtime
        ts = payload.get("ts_utc") or "unknown"
        issues = payload.get("detected_issues", [])
        status = "OK"
        if isinstance(issues, list):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                severity = str(issue.get("severity", "")).upper()
                if severity in {"HIGH", "WARN"}:
                    status = "ISSUE"
                    break
        self.action_center_status_marker_var.set(f"ACTION_CENTER_STATUS: {status}")
        self.action_center_last_report_var.set(f"LAST_REPORT_TS_UTC: {ts}")
        self._render_action_center_actions(self._extract_action_rows(payload))
        self._refresh_action_center_apply_status()
        self._refresh_action_center_doctor()

    def _extract_action_rows(self, payload: dict[str, object]) -> list[dict[str, object]]:
        rows = payload.get("action_rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        actions = payload.get("recommended_actions", [])
        default_rows: list[dict[str, object]] = []
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_id = str(action.get("action_id", ""))
                command = (
                    f"python -m tools.action_center_apply --action-id {action_id} "
                    f"--confirm {action.get('confirmation_token', '')}"
                )
                default_rows.append(
                    {
                        "action_id": action_id,
                        "severity": "INFO",
                        "risk_level": action.get("risk_level", "SAFE"),
                        "summary": action.get("title", ""),
                        "recommended_command": command,
                        "evidence_paths": action.get("related_evidence_paths", []),
                        "last_seen_ts_utc": payload.get("ts_utc", ""),
                    }
                )
        return default_rows

    def _render_action_center_actions(self, rows: list[dict[str, object]]) -> None:
        if not hasattr(self, "action_center_tree"):
            return
        self._action_center_actions = rows
        show_advanced = bool(self.action_center_show_advanced_var.get())
        visible_rows = [
            row
            for row in rows
            if show_advanced or str(row.get("risk_level", "")).upper() != "DANGEROUS"
        ]
        for item in self.action_center_tree.get_children():
            self.action_center_tree.delete(item)
        for row in visible_rows:
            evidence = row.get("evidence_paths", [])
            if isinstance(evidence, list):
                evidence_text = ", ".join(str(entry) for entry in evidence)
            else:
                evidence_text = str(evidence)
            self.action_center_tree.insert(
                "",
                tk.END,
                values=(
                    row.get("action_id", ""),
                    row.get("severity", ""),
                    row.get("risk_level", ""),
                    row.get("summary", ""),
                    evidence_text,
                    row.get("last_seen_ts_utc", ""),
                ),
            )
        if visible_rows:
            children = self.action_center_tree.get_children()
            self.action_center_tree.selection_set(children[0])
            self.action_center_tree.focus(children[0])
            self._on_action_center_select()
        else:
            self.action_center_selected_var.set("Selected action: (none)")

    def _generate_action_center_report(self) -> None:
        def runner() -> None:
            cmd = [
                sys.executable,
                "-m",
                ACTION_CENTER_REPORT_MODULE,
                "--output",
                str(ACTION_CENTER_REPORT_PATH),
            ]
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(),
            )
            result = RunResult(cmd, ROOT, proc.returncode, proc.stdout or "", proc.stderr or "")
            self._verify_output_queue.put(result.format_lines())

            def updater() -> None:
                if proc.returncode == 0:
                    messagebox.showinfo("Action Center", "Report generated.")
                else:
                    messagebox.showerror("Action Center", proc.stderr or proc.stdout or "Report generation failed.")
                self._refresh_action_center_report()

            self._enqueue_ui(updater)

        threading.Thread(target=runner, daemon=True).start()

    def _run_doctor_report(self) -> None:
        def runner() -> None:
            cmd = [sys.executable, "-m", "tools.doctor_report", "--output", str(DOCTOR_REPORT_PATH)]
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(),
            )
            result = RunResult(cmd, ROOT, proc.returncode, proc.stdout or "", proc.stderr or "")
            self._verify_output_queue.put(result.format_lines())

            def updater() -> None:
                if proc.returncode == 0:
                    messagebox.showinfo("Action Center", "Doctor report generated.")
                else:
                    messagebox.showerror("Action Center", proc.stderr or proc.stdout or "Doctor run failed.")
                self._refresh_action_center_doctor()
                self._refresh_action_center_report()

            self._enqueue_ui(updater)

        threading.Thread(target=runner, daemon=True).start()

    def _apply_safe_actions(self) -> None:
        if not self._action_center_report:
            messagebox.showinfo("Action Center", "Generate or refresh actions first.")
            return
        actions = self._action_center_report.get("recommended_actions", [])
        action_map: dict[str, dict[str, object]] = {}
        if isinstance(actions, list):
            for entry in actions:
                if isinstance(entry, dict):
                    action_map[str(entry.get("action_id", ""))] = entry
        safe_action_ids = sorted(
            action_id
            for action_id, entry in action_map.items()
            if str(entry.get("risk_level", "SAFE")).upper() == "SAFE"
        )
        if not safe_action_ids:
            messagebox.showinfo("Action Center", "No SAFE actions available.")
            return

        def runner() -> None:
            failures: list[str] = []
            for action_id in safe_action_ids:
                info = action_map.get(action_id, {})
                token = str(info.get("confirmation_token", "")).strip()
                cmd = [
                    sys.executable,
                    "-m",
                    ACTION_CENTER_APPLY_MODULE,
                    "--action-id",
                    action_id,
                    "--confirm",
                    token,
                    "--ui-confirmed",
                ]
                proc = subprocess.run(
                    cmd,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=_utf8_env(),
                )
                result = RunResult(cmd, ROOT, proc.returncode, proc.stdout or "", proc.stderr or "")
                self._verify_output_queue.put(result.format_lines())
                if proc.returncode != 0:
                    failures.append(action_id)

            def updater() -> None:
                if failures:
                    messagebox.showerror(
                        "Action Center",
                        f"Safe actions failed: {', '.join(failures)}",
                    )
                else:
                    messagebox.showinfo("Action Center", "Safe actions completed.")
                self._refresh_action_center_report()

            self._enqueue_ui(updater)

        threading.Thread(target=runner, daemon=True).start()

    def _apply_selected_action(self) -> None:
        action_id = self._selected_action_id()
        if not action_id:
            messagebox.showinfo("Action Center", "Select an action to apply.")
            return
        action_info = ACTION_CENTER_DEFAULTS.get(action_id, {}).copy()
        if self._action_center_report:
            actions = self._action_center_report.get("recommended_actions", [])
            if isinstance(actions, list):
                for entry in actions:
                    if isinstance(entry, dict) and entry.get("action_id") == action_id:
                        action_info.update(entry)
                        break
        token = str(action_info.get("confirmation_token", "")).strip()
        title = action_info.get("title", action_id)
        summary = action_info.get("effect_summary") or action_info.get("safety_notes") or ""
        risk_level = str(action_info.get("risk_level", "SAFE")).upper()

        if risk_level == "SAFE":
            self._run_action_center_action(action_id, token, ui_confirmed=True)
            return

        dialog = tk.Toplevel(self)
        dialog.title("Apply Selected Action")
        dialog.grab_set()

        tk.Label(
            dialog,
            text=f"Action: {action_id}",
            font=("Helvetica", 11, "bold"),
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(10, 4))
        tk.Label(dialog, text=title, anchor="w", wraplength=520).pack(anchor="w", padx=10)
        if summary:
            tk.Label(dialog, text=summary, fg="gray", anchor="w", wraplength=520).pack(anchor="w", padx=10, pady=4)

        hold_seconds = 1.5 if risk_level == "CAUTION" else 3.0
        hold_label = tk.Label(
            dialog,
            text=f"Press and hold Apply for {hold_seconds:.1f}s",
            anchor="w",
        )
        hold_label.pack(anchor="w", padx=10, pady=(8, 2))

        ack_var = tk.BooleanVar(value=False)
        ack_extra_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dialog,
            text="I understand (SIM-only / READ_ONLY)",
            variable=ack_var,
        ).pack(anchor="w", padx=10, pady=4)
        if risk_level == "DANGEROUS":
            tk.Checkbutton(
                dialog,
                text="I accept the risk and want to proceed",
                variable=ack_extra_var,
            ).pack(anchor="w", padx=10, pady=4)

        button_frame = tk.Frame(dialog)
        button_frame.pack(pady=10)
        confirm_btn = tk.Button(button_frame, text="Apply Action", state=tk.DISABLED)
        confirm_btn.pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        press_start: list[float] = []

        def _can_apply() -> bool:
            if not ack_var.get():
                return False
            if risk_level == "DANGEROUS" and not ack_extra_var.get():
                return False
            return True

        def _update_state(*_args: object) -> None:
            confirm_btn.configure(state=tk.NORMAL if _can_apply() else tk.DISABLED)

        def _on_press(_event=None) -> None:
            press_start.clear()
            press_start.append(time.monotonic())

        def _on_release(_event=None) -> None:
            if not _can_apply():
                return
            if not press_start:
                return
            duration = time.monotonic() - press_start[0]
            if duration >= hold_seconds:
                dialog.destroy()
                self._run_action_center_action(action_id, token, ui_confirmed=True)
            else:
                messagebox.showinfo(
                    "Action Center",
                    f"Hold Apply for at least {hold_seconds:.1f}s to confirm.",
                )

        ack_var.trace_add("write", _update_state)
        ack_extra_var.trace_add("write", _update_state)
        confirm_btn.bind("<ButtonPress-1>", _on_press)
        confirm_btn.bind("<ButtonRelease-1>", _on_release)

    def _run_action_center_action(self, action_id: str, token: str, ui_confirmed: bool = False) -> None:
        def runner() -> None:
            cmd = [
                sys.executable,
                "-m",
                ACTION_CENTER_APPLY_MODULE,
                "--action-id",
                action_id,
                "--confirm",
                token,
            ]
            if ui_confirmed:
                cmd.append("--ui-confirmed")
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(),
            )
            result = RunResult(cmd, ROOT, proc.returncode, proc.stdout or "", proc.stderr or "")
            self._verify_output_queue.put(result.format_lines())

            def updater() -> None:
                if proc.returncode == 0:
                    messagebox.showinfo("Action Center", "Action executed successfully.")
                else:
                    messagebox.showerror(
                        "Action Center",
                        proc.stderr or proc.stdout or "Action execution failed.",
                    )
                self._refresh_action_center_report()

            self._enqueue_ui(updater)

        threading.Thread(target=runner, daemon=True).start()

    def _open_evidence_pack_folder(self) -> None:
        if not ARTIFACTS_DIR.exists():
            messagebox.showinfo("Action Center", "Evidence pack folder not found.")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(ARTIFACTS_DIR))  # type: ignore[attr-defined]
                return
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(ARTIFACTS_DIR)], env=_utf8_env())
                return
            subprocess.Popen(["xdg-open", str(ARTIFACTS_DIR)], env=_utf8_env())
            return
        except Exception as exc:  # pragma: no cover - UI feedback
            self._show_copyable_text(
                "Action Center",
                f"Failed to open folder: {exc}\n\nPath: {ARTIFACTS_DIR}",
                str(ARTIFACTS_DIR),
            )

    def _copy_action_center_evidence_path(self) -> None:
        path_text = str(ARTIFACTS_DIR)
        try:
            self.clipboard_clear()
            self.clipboard_append(path_text)
            messagebox.showinfo("Action Center", "Evidence path copied to clipboard.")
        except Exception:  # pragma: no cover - UI feedback
            self._show_copyable_text("Action Center", "Copy the path below:", path_text)

    def _copy_action_center_apply_command(self) -> None:
        action_id = self._selected_action_id()
        if not action_id:
            messagebox.showinfo("Action Center", "Select an action first.")
            return
        token = ACTION_CENTER_DEFAULTS.get(action_id, {}).get("confirmation_token", "")
        python_cmd = f"\"{sys.executable}\"" if " " in sys.executable else sys.executable
        command = (
            f"{python_cmd} -m {ACTION_CENTER_APPLY_MODULE} --action-id {action_id} "
            f"--confirm {token} --dry-run"
        )
        try:
            self.clipboard_clear()
            self.clipboard_append(command)
            messagebox.showinfo("Action Center", "Apply command copied to clipboard.")
        except Exception:  # pragma: no cover - UI feedback
            self._show_copyable_text("Action Center", "Copy the command below:", command)

    def _show_copyable_text(self, title: str, message: str, payload: str) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.grab_set()
        tk.Label(dialog, text=message, justify=tk.LEFT, wraplength=520).pack(anchor="w", padx=10, pady=10)
        entry = tk.Entry(dialog, width=80)
        entry.pack(fill=tk.X, padx=10, pady=6)
        entry.insert(0, payload)
        entry.select_range(0, tk.END)
        entry.focus_set()
        tk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=6)

    def _selected_action_id(self) -> str | None:
        if not hasattr(self, "action_center_tree"):
            return None
        selection = self.action_center_tree.selection()
        if not selection:
            return None
        values = self.action_center_tree.item(selection[0]).get("values") or []
        return str(values[0]) if values else None

    def _on_action_center_select(self, event=None) -> None:  # type: ignore[override]
        action_id = self._selected_action_id()
        if action_id:
            self.action_center_selected_var.set(f"Selected action: {action_id}")
        else:
            self.action_center_selected_var.set("Selected action: (none)")

    def _refresh_action_center_apply_status(self) -> None:
        payload = self._load_json(ACTION_CENTER_APPLY_RESULT_PATH)
        ts = payload.get("ts_utc") if isinstance(payload, dict) else None
        if ts:
            self.action_center_last_apply_var.set(f"LAST_APPLY_TS_UTC: {ts}")
        else:
            self.action_center_last_apply_var.set("LAST_APPLY_TS_UTC: -")

        health_label = self.hud_data_health_var.get()
        data_health = "ISSUE"
        if "OK" in health_label:
            data_health = "OK"
        self.action_center_data_health_var.set(f"DATA_HEALTH: {data_health}")

    def _refresh_action_center_doctor(self) -> None:
        if not hasattr(self, "action_center_doctor_box"):
            return
        rows: list[str] = []
        payload = self._load_json(DOCTOR_REPORT_PATH)
        status = "ISSUE"
        ts = "-"
        if isinstance(payload, dict):
            ts = str(payload.get("ts_utc") or "-")
            runtime_health = payload.get("runtime_write_health", {})
            runtime_status = ""
            if isinstance(runtime_health, dict):
                runtime_status = str(runtime_health.get("status", ""))
            issues = payload.get("issues", [])
            if runtime_status == "FAIL":
                status = "BLOCKED"
            elif isinstance(issues, list) and issues:
                status = "ISSUE"
            else:
                status = "PASS"
        else:
            rows.append("Doctor report not found. Run Doctor to generate diagnostics.")

        self.action_center_doctor_status_var.set(f"DOCTOR_STATUS: {status}")
        self.action_center_last_doctor_var.set(f"LAST_DOCTOR_TS_UTC: {ts}")

        if isinstance(payload, dict):
            rows.append(f"Doctor report: {DOCTOR_REPORT_PATH}")
            rows.append(f"ts_utc: {payload.get('ts_utc', '-')}")
            rows.append(f"python: {payload.get('python_executable', '-')}")
            rows.append(f"python_version: {payload.get('python_version', '-')}")
            rows.append(f"venv_detected: {payload.get('venv_detected', '-')}")
            rows.append(f"kill_switch_present: {payload.get('kill_switch_present', '-')}")
            runtime_health = payload.get("runtime_write_health", {})
            if isinstance(runtime_health, dict):
                rows.append(f"runtime_write_health: {runtime_health.get('status', 'UNKNOWN')}")
            hygiene = payload.get("repo_hygiene_summary", {})
            if isinstance(hygiene, dict):
                rows.append(
                    "repo_hygiene_summary: "
                    f"status={hygiene.get('status', '-')}, "
                    f"tracked_modified={hygiene.get('tracked_modified_count', 0)}, "
                    f"untracked={hygiene.get('untracked_count', 0)}, "
                    f"runtime_artifacts={hygiene.get('runtime_artifact_count', 0)}"
                )
            issues = payload.get("issues", [])
            rows.append("")
            rows.append("Issues:")
            if isinstance(issues, list) and issues:
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    summary = issue.get("summary", "")
                    severity = issue.get("severity", "INFO")
                    evidence = issue.get("evidence_paths_rel", [])
                    rows.append(f"- [{severity}] {summary}")
                    if isinstance(evidence, list) and evidence:
                        rows.append(f"  evidence: {', '.join(str(item) for item in evidence)}")
            else:
                rows.append("- none")

        self.action_center_doctor_box.configure(state=tk.NORMAL)
        self.action_center_doctor_box.delete("1.0", tk.END)
        self.action_center_doctor_box.insert(tk.END, "\n".join(rows))
        self.action_center_doctor_box.configure(state=tk.DISABLED)

    def _load_json(self, path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _selected_progress_entry(self) -> dict[str, object] | None:
        if not hasattr(self, "progress_tree"):
            return None
        selection = self.progress_tree.selection()
        if not selection:
            return None
        index = self.progress_tree.index(selection[0])
        if index < len(self.progress_entries):
            return self.progress_entries[index]
        return None

    def _on_progress_select(self, event=None) -> None:  # type: ignore[override]
        entry = self._selected_progress_entry()
        self.progress_selected_entry = entry
        self._render_progress_detail(entry)

    def _render_progress_detail(self, entry: dict[str, object] | None) -> None:
        summary_text = "No selection"
        holdings_text = "No holdings preview"
        ascii_text = "(no equity points)"
        equity_values: List[float] = []
        equity_stats: dict[str, object] | None = None
        drawdowns: List[float] = []
        if entry:
            summary = entry.get("summary", {}) if isinstance(entry, dict) else {}
            preview = summary.get("raw_preview") if isinstance(summary, dict) else None
            missing_hint = self._progress_missing_reason(entry) if isinstance(entry, dict) else "summary_unavailable"
            if summary:
                summary_text = preview or json.dumps(summary, ensure_ascii=False, indent=2)
            else:
                summary_path = entry.get("summary_path")
                summary_text = missing_hint
                if summary_path:
                    try:
                        summary_text = Path(str(summary_path)).read_text(encoding="utf-8")
                    except Exception:
                        summary_text = missing_hint

            holdings_snapshot = entry.get("holdings_snapshot", {}) if isinstance(entry, dict) else {}
            holdings = entry.get("holdings_preview", []) if isinstance(entry, dict) else []
            if holdings_snapshot:
                positions = holdings_snapshot.get("positions", {})
                cash = holdings_snapshot.get("cash_usd", 0.0)
                holdings_text = "\n".join(f"{sym}: {qty}" for sym, qty in positions.items())
                holdings_text += f"\nCash: {cash}"
            elif holdings:
                holdings_text = "\n".join(f"{item.get('symbol', '-')}: {item.get('qty', 0)}" for item in holdings)
            else:
                holdings_text = missing_hint

            equity_points = entry.get("equity_points", []) if isinstance(entry, dict) else []
            equity_values = [float(p.get("equity", 0.0)) for p in equity_points if isinstance(p, dict)]
            if equity_values:
                ascii_text = self._sparkline_text(equity_values)
            equity_stats = entry.get("equity_stats") if isinstance(entry, dict) else None
            drawdowns = [p.get("drawdown_pct") for p in equity_points if isinstance(p, dict)]

            status = self._progress_status_label(entry)
            missing_reason = self._progress_missing_reason(entry)
            self.progress_detail_status_var.set(f"Status: {status}")
            self.progress_detail_missing_var.set(f"Missing reason: {missing_reason}")

            xp_snapshot = self._xp_snapshot_cache or load_xp_snapshot_latest(XP_SNAPSHOT_LATEST_PATH)
            if isinstance(xp_snapshot, dict) and "xp_total" in xp_snapshot and "level" in xp_snapshot:
                self.progress_judge_xp_var.set(f"Truthful XP: {xp_snapshot.get('xp_total')}")
                self.progress_judge_level_var.set(f"Level: {xp_snapshot.get('level')}")
            else:
                self.progress_judge_xp_var.set("Truthful XP: No snapshot data")
                self.progress_judge_level_var.set("Level: No snapshot data")
        else:
            self.progress_detail_status_var.set("Status: -")
            self.progress_detail_missing_var.set("Missing reason: -")
            self.progress_judge_xp_var.set("Truthful XP: No snapshot data")
            self.progress_judge_level_var.set("Level: No snapshot data")
        self.progress_summary_preview.configure(state=tk.NORMAL)
        self.progress_summary_preview.delete("1.0", tk.END)
        self.progress_summary_preview.insert(tk.END, summary_text)
        self.progress_summary_preview.configure(state=tk.DISABLED)

        self.progress_holdings_text.configure(state=tk.NORMAL)
        self.progress_holdings_text.delete("1.0", tk.END)
        self.progress_holdings_text.insert(tk.END, holdings_text)
        self.progress_holdings_text.configure(state=tk.DISABLED)

        self.progress_equity_ascii.configure(text=ascii_text)
        curve_mode = self.progress_curve_mode_var.get()
        curve_series: List[List[float]] = []
        label_values: List[float] = []
        if curve_mode == "Latest run curve":
            curve_series = [equity_values] if equity_values else []
            label_values = equity_values
        elif curve_mode == "Last N runs (concat)":
            recent = self._collect_recent_equity_series(int(self.progress_curve_runs_var.get()), window_hours=24)
            if recent:
                combined: List[float] = []
                for series in recent:
                    combined.extend(series)
                curve_series = [combined] if combined else []
                label_values = combined
        else:
            curve_series = self._collect_recent_equity_series(int(self.progress_curve_runs_var.get()), window_hours=24)
            if equity_values:
                label_values = equity_values
            elif curve_series:
                label_values = curve_series[0]

        start_val = label_values[0] if label_values else None
        end_val = label_values[-1] if label_values else None
        if isinstance(start_val, (int, float)) and isinstance(end_val, (int, float)):
            net_val = end_val - start_val
            net_text = f"{net_val:+.2f}"
            start_text = f"{start_val:.2f}"
            end_text = f"{end_val:.2f}"
        else:
            net_text = "-"
            start_text = "-"
            end_text = "-"
        max_dd = "-"
        if isinstance(equity_stats, dict):
            max_dd_val = equity_stats.get("max_drawdown")
            if isinstance(max_dd_val, (int, float)):
                max_dd = f"{max_dd_val:.2f}%"
        self.progress_equity_stats_var.set(f"Start: {start_text} | End: {end_text} | Net: {net_text} | Max DD: {max_dd}")

        drawdown_points = drawdowns if curve_mode == "Latest run curve" else None
        self._draw_equity_canvas(
            curve_series,
            stats=equity_stats,
            drawdown_points=drawdown_points,
            label_values=label_values if label_values else None,
        )

    def _latest_run_dir(self) -> Path | None:
        if not self.progress_entries:
            return None
        entry = self.progress_entries[0]
        run_dir = entry.get("run_dir") if isinstance(entry, dict) else None
        if not run_dir:
            return None
        return Path(str(run_dir))

    def _refresh_skill_tree_panel(self) -> None:
        pool_path = ROOT / "Logs" / "train_runs" / "strategy_pool.json"
        if not pool_path.exists():
            self.skill_tree_status_var.set("Skill Tree: pool manifest missing")
            self.skill_tree_detail_var.set("Pool summary: -")
            self._update_skill_tree_candidates("(no candidates yet)")
            return
        try:
            pool = json.loads(pool_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.skill_tree_status_var.set(f"Skill Tree: failed to read manifest ({exc})")
            self.skill_tree_detail_var.set("Pool summary: -")
            self._update_skill_tree_candidates("(manifest parse error)")
            return
        candidates = pool.get("candidates", []) if isinstance(pool, dict) else []
        families = pool.get("families", {}) if isinstance(pool, dict) else {}
        total = len(candidates) if isinstance(candidates, list) else 0
        family_summary = ", ".join(f"{name}:{count}" for name, count in (families or {}).items())
        self.skill_tree_status_var.set(f"Skill Tree: {total} strategies available")
        self.skill_tree_detail_var.set(f"Pool summary: {family_summary or 'unknown'}")

        latest_dir = self._latest_run_dir()
        if not latest_dir:
            self._update_skill_tree_candidates("(no latest run found)")
            return
        candidate_path = latest_dir / "candidates.json"
        if not candidate_path.exists():
            self._update_skill_tree_candidates("(candidates.json missing for latest run)")
            return
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except Exception:
            self._update_skill_tree_candidates("(candidates.json parse error)")
            return
        selected = payload.get("candidates", []) if isinstance(payload, dict) else []
        lines: List[str] = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            cid = item.get("candidate_id", "?")
            family = item.get("family", "?")
            params = item.get("params", {})
            lines.append(f"{cid} | {family} | {params}")
        self._update_skill_tree_candidates("\n".join(lines) if lines else "(no candidates selected)")

    def _update_skill_tree_candidates(self, text: str) -> None:
        if not hasattr(self, "skill_tree_candidates_text"):
            return
        self.skill_tree_candidates_text.configure(state=tk.NORMAL)
        self.skill_tree_candidates_text.delete("1.0", tk.END)
        self.skill_tree_candidates_text.insert(tk.END, text)
        self.skill_tree_candidates_text.configure(state=tk.DISABLED)

    def _refresh_upgrade_log(self) -> None:
        if not hasattr(self, "upgrade_tree"):
            return
        self.upgrade_tree.delete(*self.upgrade_tree.get_children())
        entries: List[dict[str, object]] = []
        for entry in self.progress_entries[:20]:
            if not isinstance(entry, dict):
                continue
            run_dir = entry.get("run_dir")
            if not run_dir:
                continue
            decision_path = Path(str(run_dir)) / "promotion_decision.json"
            if not decision_path.exists():
                continue
            try:
                payload = json.loads(decision_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            entry_payload = {
                "ts_utc": payload.get("ts_utc", ""),
                "run_id": entry.get("run_id", ""),
                "candidate_id": payload.get("candidate_id", ""),
                "decision": payload.get("decision", ""),
                "reasons": ", ".join(payload.get("reasons", [])) if isinstance(payload.get("reasons"), list) else "",
                "run_dir": run_dir,
                "decision_path": str(decision_path),
            }
            entries.append(entry_payload)
        self.upgrade_log_entries = entries
        for entry in entries:
            self.upgrade_tree.insert(
                "",
                tk.END,
                values=(
                    entry.get("ts_utc", ""),
                    entry.get("run_id", ""),
                    entry.get("candidate_id", ""),
                    entry.get("decision", ""),
                    entry.get("reasons", ""),
                ),
            )
        self.upgrade_log_status_var.set(
            f"Upgrade Log: {len(entries)} decision(s) loaded"
            if entries
            else "Upgrade Log: none yet (no promotion decisions)"
        )

    def _open_strategy_pool_folder(self) -> None:
        folder = ROOT / "Logs" / "train_runs"
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(folder)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Skill Tree", f"Failed to open folder: {exc}")

    def _open_selected_upgrade_run(self) -> None:
        if not hasattr(self, "upgrade_tree"):
            return
        selection = self.upgrade_tree.selection()
        if not selection:
            messagebox.showinfo("Upgrade Log", "No upgrade entry selected")
            return
        index = self.upgrade_tree.index(selection[0])
        if index >= len(self.upgrade_log_entries):
            messagebox.showinfo("Upgrade Log", "Selected entry missing")
            return
        entry = self.upgrade_log_entries[index]
        run_dir = entry.get("run_dir")
        if not run_dir:
            messagebox.showinfo("Upgrade Log", "Run directory missing")
            return
        try:
            path = Path(str(run_dir))
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Upgrade Log", f"Failed to open run dir: {exc}")

    def _open_selected_upgrade_decision(self) -> None:
        if not hasattr(self, "upgrade_tree"):
            return
        selection = self.upgrade_tree.selection()
        if not selection:
            messagebox.showinfo("Upgrade Log", "No upgrade entry selected")
            return
        index = self.upgrade_tree.index(selection[0])
        if index >= len(self.upgrade_log_entries):
            messagebox.showinfo("Upgrade Log", "Selected entry missing")
            return
        entry = self.upgrade_log_entries[index]
        decision_path = entry.get("decision_path")
        if not decision_path:
            messagebox.showinfo("Upgrade Log", "Decision path missing")
            return
        path = Path(str(decision_path))
        if not path.exists():
            messagebox.showinfo("Upgrade Log", "Decision file not found")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Upgrade Log", f"Failed to open decision: {exc}")

    def _open_progress_folder(self) -> None:
        folder = self.progress_index_path.parent
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(folder)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Progress", f"Failed to open folder: {exc}")

    def _open_progress_index_file(self) -> None:
        if not self.progress_index_path.exists():
            messagebox.showinfo("Progress", "progress_index.json not found")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(self.progress_index_path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(self.progress_index_path)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Progress", f"Failed to open index: {exc}")

    def _open_selected_run_dir(self) -> None:
        entry = self._selected_progress_entry()
        if not entry:
            messagebox.showinfo("Progress", "No run selected")
            return
        run_dir = entry.get("run_dir")
        if not run_dir:
            messagebox.showinfo("Progress", "Run directory missing")
            return
        try:
            path = Path(str(run_dir))
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Progress", f"Failed to open run dir: {exc}")

    def _open_selected_summary(self) -> None:
        entry = self._selected_progress_entry()
        if not entry:
            messagebox.showinfo("Progress", "No run selected")
            return
        summary_path = entry.get("summary_path")
        if not summary_path:
            messagebox.showinfo("Progress", "Summary path missing")
            return
        path = Path(str(summary_path))
        if not path.exists():
            messagebox.showinfo("Progress", "Summary file not found")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Progress", f"Failed to open summary: {exc}")

    def _open_selected_equity(self) -> None:
        entry = self._selected_progress_entry()
        equity_path = entry.get("equity_path") if entry else None
        if not equity_path:
            messagebox.showinfo("Progress", "Equity curve missing for selection")
            return
        path = Path(str(equity_path))
        if not path.exists():
            messagebox.showinfo("Progress", "Equity curve file not found")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Progress", f"Failed to open equity curve: {exc}")

    def _export_progress_chart(self) -> None:
        if not hasattr(self, "progress_equity_canvas"):
            return
        entry = self.progress_selected_entry or (self.progress_entries[0] if self.progress_entries else None)
        run_dir = entry.get("run_dir") if isinstance(entry, dict) else None
        if not run_dir:
            messagebox.showinfo("Progress", "Run directory not available for export")
            return
        chart_path = Path(str(run_dir)) / "chart.ps"
        try:
            self.progress_equity_canvas.postscript(file=str(chart_path))
            messagebox.showinfo("Progress", f"Chart exported to {chart_path}")
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Progress", f"Failed to export chart: {exc}")

    def _build_progress_tab(self) -> None:
        proof_frame = tk.LabelFrame(self.progress_tab, text="Proof Lamps (SIM-only)", padx=6, pady=6)
        proof_frame.pack(fill=tk.X, padx=6, pady=4)
        row = tk.Frame(proof_frame)
        row.pack(fill=tk.X)

        def build_lamp(
            parent: tk.Frame,
            title: str,
            status_var: tk.StringVar,
            detail_var: tk.StringVar,
            button_text: str | None = None,
            command=None,
        ) -> tk.Label:
            frame = tk.Frame(parent)
            frame.pack(side=tk.LEFT, padx=10, pady=2)
            tk.Label(frame, text=title, font=("Helvetica", 11, "bold")).pack(anchor="w")
            lamp = tk.Label(frame, text="UNKNOWN", width=12, relief=tk.SOLID, fg="white", bg="#555")
            lamp.pack(anchor="w", pady=2)
            tk.Label(frame, textvariable=status_var, anchor="w").pack(anchor="w")
            tk.Label(frame, textvariable=detail_var, anchor="w").pack(anchor="w")
            if button_text and command:
                tk.Button(frame, text=button_text, command=command).pack(anchor="w", pady=2)
            return lamp

        self.proof_baseline_lamp = build_lamp(
            row,
            "Baseline",
            self.proof_baseline_status_var,
            self.proof_baseline_detail_var,
            button_text="Open Guide",
            command=self._open_baseline_guide,
        )
        self.proof_service_lamp = build_lamp(
            row,
            "Training Service",
            self.proof_service_status_var,
            self.proof_service_detail_var,
        )
        self.proof_judge_lamp = build_lamp(
            row,
            "Judge Freshness",
            self.proof_judge_status_var,
            self.proof_judge_detail_var,
        )
        self.proof_ui_smoke_lamp = build_lamp(
            row,
            "UI Smoke",
            self.proof_ui_smoke_status_var,
            self.proof_ui_smoke_detail_var,
        )

        banner = tk.Label(
            self.progress_tab,
            text="Progress panel (SIM-only). Uses Logs/train_runs/progress_index.json to render training snapshots.",
            fg="#2563eb",
            anchor="w",
        )
        banner.pack(fill=tk.X, padx=6, pady=4)
        auto_frame = tk.LabelFrame(self.progress_tab, text="Auto-refresh", padx=6, pady=6)
        auto_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Checkbutton(
            auto_frame, text="Auto-refresh", variable=self.progress_auto_refresh_var
        ).grid(row=0, column=0, sticky="w", padx=4)
        tk.Label(auto_frame, text="Interval (s)").grid(row=0, column=1, sticky="w")
        ttk.OptionMenu(
            auto_frame,
            self.progress_refresh_interval_var,
            self.progress_refresh_interval_var.get(),
            5,
            15,
            30,
        ).grid(row=0, column=2, sticky="w", padx=4)
        tk.Label(auto_frame, textvariable=self.progress_last_refresh_var, anchor="w").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=4
        )
        tk.Label(auto_frame, textvariable=self.progress_last_new_run_var, anchor="w").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=4
        )
        truthful_frame = tk.LabelFrame(self.progress_tab, text="Truthful Progress (Judge)", padx=6, pady=6)
        truthful_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(truthful_frame, textvariable=self.progress_truth_status_var, font=("Helvetica", 13, "bold")).pack(
            anchor="w"
        )
        score_row = tk.Frame(truthful_frame)
        score_row.pack(fill=tk.X, pady=2)
        tk.Label(score_row, textvariable=self.progress_truth_score_do_nothing_var, font=("Helvetica", 12, "bold")).pack(
            side=tk.LEFT, padx=4
        )
        tk.Label(score_row, textvariable=self.progress_truth_score_buy_hold_var, font=("Helvetica", 12, "bold")).pack(
            side=tk.LEFT, padx=12
        )
        tk.Label(truthful_frame, textvariable=self.progress_truth_trend_var, anchor="w").pack(anchor="w")
        tk.Label(
            truthful_frame,
            textvariable=self.progress_truth_why_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
        ).pack(anchor="w")
        tk.Label(
            truthful_frame,
            textvariable=self.progress_truth_not_improving_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
        ).pack(anchor="w")
        tk.Label(
            truthful_frame,
            textvariable=self.progress_truth_action_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
        ).pack(anchor="w")
        tk.Label(
            truthful_frame,
            textvariable=self.progress_truth_evidence_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
            fg="#6b7280",
        ).pack(anchor="w")
        friction_frame = tk.LabelFrame(self.progress_tab, text="Execution Friction + Stress (SIM-only)", padx=6, pady=6)
        friction_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(friction_frame, textvariable=self.progress_friction_policy_var, anchor="w").pack(anchor="w")
        tk.Label(
            friction_frame,
            textvariable=self.progress_friction_detail_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
        ).pack(anchor="w")
        tk.Label(friction_frame, textvariable=self.progress_stress_status_var, anchor="w").pack(anchor="w")
        tk.Label(
            friction_frame,
            textvariable=self.progress_stress_reject_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
        ).pack(anchor="w")
        tk.Label(
            friction_frame,
            textvariable=self.progress_stress_evidence_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
            fg="#6b7280",
        ).pack(anchor="w")
        xp_frame = tk.LabelFrame(self.progress_tab, text="Truthful XP (SIM-only)", padx=6, pady=6)
        xp_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(xp_frame, textvariable=self.progress_xp_status_var, font=("Helvetica", 13, "bold")).pack(
            anchor="w"
        )
        tk.Label(xp_frame, textvariable=self.progress_xp_level_var, font=("Helvetica", 12, "bold")).pack(
            anchor="w"
        )
        self.progress_xp_progress = ttk.Progressbar(xp_frame, orient="horizontal", length=320, mode="determinate")
        self.progress_xp_progress.pack(fill=tk.X, padx=4, pady=2)
        tk.Label(
            xp_frame,
            textvariable=self.progress_xp_banner_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
            fg="#b91c1c",
        ).pack(anchor="w")
        xp_table_frame = tk.Frame(xp_frame)
        xp_table_frame.pack(fill=tk.X, pady=4)
        xp_columns = ("label", "points", "value", "evidence")
        self.progress_xp_tree = ttk.Treeview(xp_table_frame, columns=xp_columns, show="headings", height=5)
        for name, width in [
            ("label", 220),
            ("points", 80),
            ("value", 140),
            ("evidence", 420),
        ]:
            self.progress_xp_tree.heading(name, text=name)
            self.progress_xp_tree.column(name, width=width, anchor="w")
        self.progress_xp_tree.pack(fill=tk.X, expand=True)
        xp_actions = tk.Frame(xp_frame)
        xp_actions.pack(fill=tk.X, pady=2)
        tk.Button(xp_actions, text="Open XP Evidence", command=self._open_xp_evidence).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(xp_actions, textvariable=self.progress_xp_evidence_var, anchor="w", fg="#6b7280").pack(
            side=tk.LEFT, padx=6, fill=tk.X, expand=True
        )
        diag_frame = tk.LabelFrame(self.progress_tab, text="Run-rate diagnosis (SIM-only)", padx=6, pady=6)
        diag_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(diag_frame, textvariable=self.progress_diag_status_var, anchor="w", font=("Helvetica", 12, "bold")).pack(
            anchor="w"
        )
        tk.Label(
            diag_frame,
            textvariable=self.progress_diag_summary_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
        ).pack(anchor="w")

        growth_frame = tk.LabelFrame(self.progress_tab, text="Growth HUD", padx=6, pady=6)
        growth_frame.pack(fill=tk.X, padx=6, pady=4)
        hud_font = ("Helvetica", 12, "bold")
        tk.Label(growth_frame, textvariable=self.progress_growth_total_var, font=hud_font).grid(row=0, column=0, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_runs_today_var, font=hud_font).grid(row=0, column=1, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_last_net_var, font=hud_font).grid(row=1, column=0, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_cash_last_var, font=hud_font).grid(row=1, column=1, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_cash_24h_var, font=hud_font).grid(row=2, column=0, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_seven_day_var, font=hud_font).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_max_dd_var, font=hud_font).grid(row=3, column=0, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_rejects_var, font=hud_font).grid(row=3, column=1, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_gates_var, font=hud_font).grid(row=4, column=0, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_service_var, font=hud_font).grid(row=4, column=1, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_kill_var, font=hud_font).grid(row=5, column=0, sticky="w", padx=6, pady=2)
        tk.Label(growth_frame, textvariable=self.progress_growth_truth_xp_var, font=hud_font).grid(row=5, column=1, sticky="w", padx=6, pady=2)

        throughput_frame = tk.LabelFrame(self.progress_tab, text="Throughput (SIM-only)", padx=6, pady=6)
        throughput_frame.pack(fill=tk.X, padx=6, pady=4)
        self.progress_throughput_lamp = tk.Label(
            throughput_frame, text="STALE", width=8, relief=tk.SOLID, fg="white", bg="#555"
        )
        self.progress_throughput_lamp.pack(side=tk.LEFT, padx=4, pady=2)
        throughput_text = tk.Frame(throughput_frame)
        throughput_text.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)
        tk.Label(throughput_text, textvariable=self.progress_run_rate_var, anchor="w").pack(anchor="w")
        tk.Label(throughput_text, textvariable=self.progress_run_stale_var, anchor="w").pack(anchor="w")
        tk.Label(throughput_text, textvariable=self.progress_throughput_diag_var, anchor="w").pack(anchor="w")
        throughput_actions = tk.Frame(throughput_frame)
        throughput_actions.pack(side=tk.RIGHT, padx=4)
        tk.Button(
            throughput_actions,
            text="Run Diagnose Now",
            command=self._run_throughput_diagnose,
        ).pack(side=tk.TOP, pady=2)
        tk.Button(
            throughput_actions,
            text="Open Diagnose Report",
            command=self._open_throughput_report,
        ).pack(side=tk.TOP, pady=2)
        self.progress_throughput_output = ScrolledText(throughput_frame, height=4, wrap=tk.WORD)
        self.progress_throughput_output.pack(fill=tk.X, padx=4, pady=4)
        self.progress_throughput_output.configure(state=tk.DISABLED)

        engine_frame = tk.LabelFrame(self.progress_tab, text="Engine Status", padx=6, pady=6)
        engine_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(engine_frame, textvariable=self.engine_status_tournament_var, anchor="w").pack(anchor="w")
        tk.Label(engine_frame, textvariable=self.engine_status_promotion_var, anchor="w").pack(anchor="w")
        tk.Label(engine_frame, textvariable=self.engine_status_judge_var, anchor="w").pack(anchor="w")

        pr28_frame = tk.LabelFrame(self.progress_tab, text="PR28 Training Loop (SIM-only)", padx=6, pady=6)
        pr28_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(pr28_frame, textvariable=self.pr28_tournament_status_var, anchor="w").pack(anchor="w")
        tk.Label(pr28_frame, textvariable=self.pr28_judge_status_var, anchor="w").pack(anchor="w")
        tk.Label(pr28_frame, textvariable=self.pr28_promotion_status_var, anchor="w").pack(anchor="w")
        tk.Label(pr28_frame, textvariable=self.pr28_evidence_var, anchor="w", fg="#6b7280").pack(anchor="w")

        status_label = tk.Label(self.progress_tab, textvariable=self.progress_status_var, anchor="w")
        status_label.pack(fill=tk.X, padx=6)

        button_frame = tk.Frame(self.progress_tab)
        button_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Button(button_frame, text="Generate index", command=self._handle_generate_progress_index).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(button_frame, text="Refresh view", command=self._handle_refresh_progress_view).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(button_frame, text="Open progress folder", command=self._open_progress_folder).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(button_frame, text="Open index file", command=self._open_progress_index_file).pack(
            side=tk.LEFT, padx=4
        )

        policy_frame = tk.LabelFrame(self.progress_tab, text="Policy History (SIM-only)", padx=6, pady=6)
        policy_frame.pack(fill=tk.BOTH, expand=False, padx=6, pady=4)
        tk.Label(policy_frame, textvariable=self.policy_history_status_var, anchor="w").pack(anchor="w")
        policy_columns = ("ts", "policy_version", "decision", "reason")
        self.policy_tree = ttk.Treeview(policy_frame, columns=policy_columns, show="headings", height=5)
        for name, width in [
            ("ts", 160),
            ("policy_version", 140),
            ("decision", 120),
            ("reason", 360),
        ]:
            self.policy_tree.heading(name, text=name)
            self.policy_tree.column(name, width=width, anchor="w")
        self.policy_tree.pack(fill=tk.BOTH, expand=True)
        policy_actions = tk.Frame(policy_frame)
        policy_actions.pack(fill=tk.X, pady=2)
        tk.Button(policy_actions, text="Open evidence", command=self._open_policy_evidence).pack(
            side=tk.LEFT, padx=3
        )
        tk.Button(policy_actions, text="Refresh history", command=self._refresh_policy_history).pack(
            side=tk.LEFT, padx=3
        )

        skill_frame = tk.LabelFrame(self.progress_tab, text="Skill Tree (Strategy Pool)", padx=6, pady=6)
        skill_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(skill_frame, textvariable=self.skill_tree_status_var, anchor="w").pack(anchor="w")
        tk.Label(skill_frame, textvariable=self.skill_tree_detail_var, anchor="w", fg="#6b7280").pack(anchor="w")
        tk.Label(skill_frame, text="Currently tested candidates:", anchor="w").pack(anchor="w")
        self.skill_tree_candidates_text = ScrolledText(skill_frame, height=4, wrap=tk.WORD)
        self.skill_tree_candidates_text.pack(fill=tk.X, padx=2, pady=2)
        self.skill_tree_candidates_text.configure(state=tk.DISABLED)
        tk.Label(
            skill_frame,
            text="What this means: candidates advance only when they repeatedly beat baselines without violating safety gates.",
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
        ).pack(anchor="w")
        skill_buttons = tk.Frame(skill_frame)
        skill_buttons.pack(fill=tk.X, pady=2)
        tk.Button(skill_buttons, text="Open pool manifest folder", command=self._open_strategy_pool_folder).pack(
            side=tk.LEFT, padx=3
        )

        upgrade_frame = tk.LabelFrame(self.progress_tab, text="Upgrade Log (Promotion Decisions)", padx=6, pady=6)
        upgrade_frame.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(upgrade_frame, textvariable=self.upgrade_log_status_var, anchor="w").pack(anchor="w")
        upgrade_columns = ("ts", "run_id", "candidate_id", "decision", "reasons")
        self.upgrade_tree = ttk.Treeview(upgrade_frame, columns=upgrade_columns, show="headings", height=4)
        for name, width in [
            ("ts", 160),
            ("run_id", 140),
            ("candidate_id", 160),
            ("decision", 110),
            ("reasons", 360),
        ]:
            self.upgrade_tree.heading(name, text=name)
            self.upgrade_tree.column(name, width=width, anchor="w")
        self.upgrade_tree.pack(fill=tk.X, expand=True)
        upgrade_actions = tk.Frame(upgrade_frame)
        upgrade_actions.pack(fill=tk.X, pady=2)
        tk.Button(upgrade_actions, text="Open run folder", command=self._open_selected_upgrade_run).pack(
            side=tk.LEFT, padx=3
        )
        tk.Button(upgrade_actions, text="Open decision JSON", command=self._open_selected_upgrade_decision).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(
            upgrade_frame,
            text="What this means: promotions are advisory by default and prioritize risk limits over raw returns.",
            anchor="w",
            justify=tk.LEFT,
            wraplength=1000,
            fg="#6b7280",
        ).pack(anchor="w")

        container = tk.Frame(self.progress_tab)
        container.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        left = tk.Frame(container)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = tk.Frame(container)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        runs_frame = tk.LabelFrame(left, text="Runs (SIM-only)", padx=4, pady=4)
        runs_frame.pack(fill=tk.BOTH, expand=True, padx=4)
        columns = ("run_id", "status", "net_change", "stop_reason", "mtime")
        self.progress_tree = ttk.Treeview(runs_frame, columns=columns, show="headings", height=8)
        for name, width in [
            ("run_id", 140),
            ("status", 120),
            ("net_change", 120),
            ("stop_reason", 140),
            ("mtime", 180),
        ]:
            self.progress_tree.heading(name, text=name)
            self.progress_tree.column(name, width=width, anchor="w")
        self.progress_tree.pack(fill=tk.BOTH, expand=True)
        self.progress_tree.bind("<<TreeviewSelect>>", self._on_progress_select)

        actions = tk.Frame(runs_frame)
        actions.pack(fill=tk.X, pady=4)
        tk.Button(actions, text="Open run folder", command=self._open_selected_run_dir).pack(side=tk.LEFT, padx=3)
        tk.Button(actions, text="Open summary", command=self._open_selected_summary).pack(side=tk.LEFT, padx=3)
        tk.Button(actions, text="Open equity_curve.csv", command=self._open_selected_equity).pack(side=tk.LEFT, padx=3)

        detail_frame = tk.LabelFrame(right, text="Details", padx=4, pady=4)
        detail_frame.pack(fill=tk.BOTH, expand=True, padx=4)
        status_frame = tk.Frame(detail_frame)
        status_frame.pack(fill=tk.X, pady=4)
        tk.Label(status_frame, textvariable=self.progress_detail_status_var, anchor="w").pack(anchor="w")
        tk.Label(status_frame, textvariable=self.progress_detail_missing_var, anchor="w").pack(anchor="w")

        judge_frame = tk.LabelFrame(detail_frame, text="Truthful XP/Level (snapshot)", padx=4, pady=4)
        judge_frame.pack(fill=tk.X, pady=4)
        tk.Label(judge_frame, textvariable=self.progress_judge_xp_var, anchor="w").pack(anchor="w")
        tk.Label(judge_frame, textvariable=self.progress_judge_level_var, anchor="w").pack(anchor="w")
        tk.Label(detail_frame, text="Summary preview:").pack(anchor="w")
        self.progress_summary_preview = ScrolledText(detail_frame, height=8, wrap=tk.WORD)
        self.progress_summary_preview.pack(fill=tk.BOTH, expand=True)
        self.progress_summary_preview.configure(state=tk.DISABLED)

        holdings_frame = tk.Frame(detail_frame)
        holdings_frame.pack(fill=tk.X, pady=4)
        tk.Label(holdings_frame, text="Holdings preview:").pack(anchor="w")
        self.progress_holdings_text = tk.Text(holdings_frame, height=3, width=50)
        self.progress_holdings_text.pack(fill=tk.X)
        self.progress_holdings_text.configure(state=tk.DISABLED)

        equity_frame = tk.Frame(detail_frame)
        equity_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        tk.Label(equity_frame, text="Equity sparkline (ASCII + canvas)").pack(anchor="w")
        controls = tk.Frame(equity_frame)
        controls.pack(fill=tk.X, pady=2)
        ttk.OptionMenu(
            controls,
            self.progress_curve_mode_var,
            self.progress_curve_mode_var.get(),
            "Latest run curve",
            "Last N runs (concat)",
            "Last N runs (overlay)",
        ).pack(side=tk.LEFT, padx=4)
        tk.Label(controls, text="Runs").pack(side=tk.LEFT)
        tk.Spinbox(controls, from_=1, to=20, textvariable=self.progress_curve_runs_var, width=4).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(controls, text="Export Chart", command=self._export_progress_chart).pack(side=tk.RIGHT, padx=4)
        tk.Label(equity_frame, text="Curve modes use last 24h runs for rolling views (SIM-only).", fg="#6b7280").pack(
            anchor="w"
        )
        tk.Label(equity_frame, textvariable=self.progress_equity_stats_var, anchor="w").pack(anchor="w")
        self.progress_equity_ascii = tk.Label(equity_frame, font=("Courier", 10), anchor="w", justify=tk.LEFT)
        self.progress_equity_ascii.pack(fill=tk.X)
        self.progress_equity_canvas = tk.Canvas(equity_frame, height=140, bg="#f8fafc")
        self.progress_equity_canvas.pack(fill=tk.BOTH, expand=True)

        self.progress_curve_mode_var.trace_add("write", lambda *_: self._render_progress_detail(self.progress_selected_entry))
        self.progress_curve_runs_var.trace_add("write", lambda *_: self._render_progress_detail(self.progress_selected_entry))

        self._refresh_proof_lamps()
        self._load_progress_index()
        self._refresh_truthful_progress()
        self._refresh_friction_status()
        self._refresh_xp_snapshot()
        self._refresh_engine_status()
        self._refresh_policy_history()
        self._refresh_skill_tree_panel()
        self._refresh_upgrade_log()

    def _format_score(self, value: object) -> str:
        if isinstance(value, (int, float)):
            return f"{value:+.2f}"
        return "N/A"

    def _refresh_truthful_progress(self) -> None:
        payload = load_progress_judge_latest(
            PROGRESS_JUDGE_LATEST_PATH, fallback=LEGACY_PROGRESS_JUDGE_LATEST_PATH
        )
        recommendation = payload.get("recommendation", "INSUFFICIENT_DATA")
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        source_mode = source.get("mode", "unknown")
        source_path = source.get("path", "")
        scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
        drivers = payload.get("drivers") if isinstance(payload.get("drivers"), list) else []
        not_improving = payload.get("not_improving_reasons") if isinstance(payload.get("not_improving_reasons"), list) else []
        actions = payload.get("suggested_next_actions") if isinstance(payload.get("suggested_next_actions"), list) else []
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        trend = payload.get("trend") if isinstance(payload.get("trend"), dict) else {}

        score_do_nothing = self._format_score(scores.get("vs_do_nothing") if isinstance(scores, dict) else None)
        score_buy_hold = self._format_score(scores.get("vs_buy_hold") if isinstance(scores, dict) else None)
        trend_dir = trend.get("direction", "unknown")
        trend_window = trend.get("window", 0)

        self.progress_truth_status_var.set(
            f"Truthful Progress: {recommendation} (source: {source_mode}{' ' + source_path if source_path else ''})"
        )
        self.progress_truth_score_do_nothing_var.set(f"Score vs DoNothing: {score_do_nothing}")
        self.progress_truth_score_buy_hold_var.set(f"Score vs Buy&Hold: {score_buy_hold}")
        self.progress_truth_trend_var.set(f"Trend (last {trend_window}): {trend_dir}")
        self.progress_truth_why_var.set(f"Why: {', '.join(drivers) if drivers else 'No drivers available'}")
        self.progress_truth_not_improving_var.set(
            f"Not improving because: {', '.join(not_improving) if not_improving else '—'}"
        )
        self.progress_truth_action_var.set(
            f"Suggested action: {', '.join(actions) if actions else 'No action suggestions'}"
        )
        if payload.get("status") == "missing":
            missing_artifacts = payload.get("missing_artifacts", [])
            searched = payload.get("searched_paths", [])
            missing_text = ", ".join(str(item) for item in missing_artifacts) if missing_artifacts else "-"
            searched_text = ", ".join(str(item) for item in searched) if searched else "-"
            self.progress_truth_action_var.set(
                f"Suggested action: {', '.join(actions) if actions else 'No action suggestions'} | "
                f"Missing: {missing_text} | Looked: {searched_text}"
            )
        evidence_ids = evidence.get("run_ids") if isinstance(evidence, dict) else []
        if isinstance(evidence_ids, list) and evidence_ids:
            self.progress_truth_evidence_var.set(f"Evidence runs: {', '.join([str(rid) for rid in evidence_ids])}")
        else:
            self.progress_truth_evidence_var.set("Evidence runs: none")

    def _refresh_friction_status(self) -> None:
        policy_payload = {}
        if FRICTION_POLICY_PATH.exists():
            try:
                policy_payload = json.loads(FRICTION_POLICY_PATH.read_text(encoding="utf-8"))
            except Exception:
                policy_payload = {}
        policy_path_text = to_repo_relative(FRICTION_POLICY_PATH)
        if policy_payload:
            fee_trade = policy_payload.get("fee_per_trade", "-")
            fee_share = policy_payload.get("fee_per_share", "-")
            spread = policy_payload.get("spread_bps", "-")
            slippage = policy_payload.get("slippage_bps", "-")
            latency = policy_payload.get("latency_ms", "-")
            partial_prob = policy_payload.get("partial_fill_prob", "-")
            max_fraction = policy_payload.get("max_fill_fraction", "-")
            self.progress_friction_policy_var.set(f"Friction policy: {policy_path_text}")
            self.progress_friction_detail_var.set(
                "Friction settings: "
                f"fee_per_trade={fee_trade} fee_per_share={fee_share} | "
                f"spread_bps={spread} slippage_bps={slippage} | "
                f"latency_ms={latency} | partial_fill_prob={partial_prob} max_fill_fraction={max_fraction}"
            )
        else:
            self.progress_friction_policy_var.set(f"Friction policy: missing ({policy_path_text})")
            self.progress_friction_detail_var.set("Friction settings: -")

        stress_payload = {}
        if LATEST_STRESS_REPORT_PATH.exists():
            try:
                stress_payload = json.loads(LATEST_STRESS_REPORT_PATH.read_text(encoding="utf-8"))
            except Exception:
                stress_payload = {}
        if not stress_payload:
            self.progress_stress_status_var.set(
                f"Stress status: missing ({to_repo_relative(LATEST_STRESS_REPORT_PATH)})"
            )
            self.progress_stress_reject_var.set("Stress reject reasons: stress_report_missing")
            self.progress_stress_evidence_var.set("Stress evidence: -")
            return

        status = stress_payload.get("status", "UNKNOWN")
        run_id = stress_payload.get("run_id", "-")
        self.progress_stress_status_var.set(f"Stress status: {status} (run_id={run_id})")
        fail_reasons = stress_payload.get("fail_reasons", [])
        if isinstance(fail_reasons, list) and fail_reasons:
            self.progress_stress_reject_var.set(
                f"Stress reject reasons: {', '.join(str(item) for item in fail_reasons)}"
            )
        else:
            self.progress_stress_reject_var.set("Stress reject reasons: none")
        evidence = stress_payload.get("evidence", {})
        if isinstance(evidence, dict) and evidence:
            evidence_parts = [f"{key}={value}" for key, value in evidence.items()]
            self.progress_stress_evidence_var.set(f"Stress evidence: {', '.join(evidence_parts)}")
        else:
            self.progress_stress_evidence_var.set("Stress evidence: -")

    def _refresh_xp_snapshot(self) -> None:
        payload = load_xp_snapshot_latest(XP_SNAPSHOT_LATEST_PATH)
        self._xp_snapshot_cache = payload if isinstance(payload, dict) else None
        status = payload.get("status", "INSUFFICIENT_DATA")
        xp_total = payload.get("xp_total")
        level = payload.get("level")
        level_progress = payload.get("level_progress", 0.0)
        if isinstance(xp_total, (int, float)):
            xp_text = f"Truthful XP: {int(xp_total)}"
        else:
            xp_text = "Truthful XP: -"
        if isinstance(level, (int, float)):
            level_text = f"Level: {int(level)}"
        else:
            level_text = "Level: -"
        self.progress_xp_status_var.set(f"{xp_text} | status {status}")
        self.progress_xp_level_var.set(level_text)
        try:
            progress_value = float(level_progress) * 100.0
        except Exception:
            progress_value = 0.0
        self.progress_xp_progress["value"] = max(0.0, min(100.0, progress_value))

        missing_reasons = payload.get("missing_reasons", [])
        if isinstance(missing_reasons, list) and missing_reasons:
            banner = f"INSUFFICIENT_DATA: {', '.join(str(item) for item in missing_reasons[:3])}"
        else:
            banner = ""
        self.progress_xp_banner_var.set(banner)

        evidence_paths: list[str] = []
        breakdown = payload.get("xp_breakdown", [])
        if isinstance(breakdown, list):
            for item in breakdown:
                if not isinstance(item, dict):
                    continue
                paths = item.get("evidence_paths_rel")
                if isinstance(paths, list):
                    evidence_paths.extend(str(p) for p in paths if p)
        evidence_paths = list(dict.fromkeys(evidence_paths))
        if evidence_paths:
            self.progress_xp_evidence_var.set(f"Evidence paths: {', '.join(evidence_paths[:4])}")
        else:
            self.progress_xp_evidence_var.set("Evidence paths: -")

        if hasattr(self, "progress_xp_tree"):
            for item in self.progress_xp_tree.get_children():
                self.progress_xp_tree.delete(item)
            if isinstance(breakdown, list):
                for row in breakdown:
                    if not isinstance(row, dict):
                        continue
                    label = row.get("label", "")
                    points = row.get("points", "")
                    value = row.get("value", "")
                    evidence = row.get("evidence_paths_rel", [])
                    evidence_text = ", ".join(str(p) for p in evidence) if isinstance(evidence, list) else str(evidence)
                    self.progress_xp_tree.insert(
                        "", tk.END, values=(label, points, value, evidence_text)
                    )

    def _open_xp_evidence(self) -> None:
        if XP_SNAPSHOT_LATEST_PATH.exists():
            try:
                if hasattr(os, "startfile"):
                    os.startfile(str(XP_SNAPSHOT_LATEST_PATH.parent))  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["xdg-open", str(XP_SNAPSHOT_LATEST_PATH.parent)], env=_utf8_env())
                return
            except Exception as exc:  # pragma: no cover - UI feedback
                messagebox.showerror("XP Evidence", f"Failed to open XP folder: {exc}")
                return
        payload = self._xp_snapshot_cache or {}
        evidence_paths = payload.get("source_artifacts", {}) if isinstance(payload, dict) else {}
        message = "XP snapshot not found.\n"
        if isinstance(evidence_paths, dict) and evidence_paths:
            message += "Evidence paths:\n" + "\n".join(str(p) for p in evidence_paths.values())
        else:
            message += "Evidence paths unavailable."
        messagebox.showinfo("XP Evidence", message)

    def _refresh_engine_status(self) -> None:
        judge_path = PROGRESS_JUDGE_LATEST_PATH if PROGRESS_JUDGE_LATEST_PATH.exists() else LEGACY_PROGRESS_JUDGE_LATEST_PATH
        status = load_engine_status(
            LATEST_TOURNAMENT_PATH,
            LATEST_PROMOTION_DECISION_PATH,
            judge_path,
        )
        pr28_status = load_pr28_latest(
            PR28_TOURNAMENT_RESULT_LATEST_PATH,
            PR28_JUDGE_RESULT_LATEST_PATH,
            PR28_PROMOTION_DECISION_LATEST_PATH,
            PR28_PROMOTION_HISTORY_LATEST_PATH,
        )

        def _format_status(label: str, payload: dict[str, object]) -> str:
            created = payload.get("created_utc") or payload.get("generated_ts") or "-"
            reason = payload.get("missing_reason")
            source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
            source_mode = source.get("mode", "unknown")
            source_path = source.get("path", "")
            if payload.get("status") == "missing":
                missing_artifacts = payload.get("missing_artifacts", [])
                searched = payload.get("searched_paths", [])
                missing_text = ", ".join(str(item) for item in missing_artifacts) if missing_artifacts else "-"
                searched_text = ", ".join(str(item) for item in searched) if searched else "-"
                next_actions = payload.get("suggested_next_actions", [])
                next_text = ", ".join(str(item) for item in next_actions) if next_actions else "-"
                return (
                    f"{label}: missing ({reason}) | source={source_mode} {source_path} | "
                    f"missing={missing_text} | looked={searched_text} | next={next_text}"
                )
            if reason:
                return f"{label}: {created} (missing fields: {reason}) | source={source_mode} {source_path}"
            return f"{label}: {created} | source={source_mode} {source_path}"

        self.engine_status_tournament_var.set(
            _format_status("Last tournament updated", status.get("tournament", {}))
        )
        self.engine_status_promotion_var.set(
            _format_status("Last promotion decision", status.get("promotion", {}))
        )
        self.engine_status_judge_var.set(
            _format_status("Last judge updated", status.get("judge", {}))
        )

        def _format_pr28(label: str, payload: dict[str, object]) -> str:
            ts = payload.get("ts_utc") or payload.get("created_utc") or "-"
            reason = payload.get("missing_reason")
            source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
            source_mode = source.get("mode", "unknown")
            source_path = source.get("path", "")
            if payload.get("status") == "missing":
                missing_artifacts = payload.get("missing_artifacts", [])
                searched = payload.get("searched_paths", [])
                missing_text = ", ".join(str(item) for item in missing_artifacts) if missing_artifacts else "-"
                searched_text = ", ".join(str(item) for item in searched) if searched else "-"
                next_actions = payload.get("suggested_next_actions", [])
                next_text = ", ".join(str(item) for item in next_actions) if next_actions else "-"
                return (
                    f"{label}: missing ({reason}) | source={source_mode} {source_path} | "
                    f"missing={missing_text} | looked={searched_text} | next={next_text}"
                )
            if reason:
                return f"{label}: {ts} (missing fields: {reason}) | source={source_mode} {source_path}"
            return f"{label}: {ts} | source={source_mode} {source_path}"

        self.pr28_tournament_status_var.set(
            _format_pr28("PR28 tournament", pr28_status.get("tournament", {}))
        )
        judge_payload = pr28_status.get("judge", {})
        judge_status = str(judge_payload.get("status") or "unknown")
        scores = judge_payload.get("scores", {}) if isinstance(judge_payload.get("scores"), dict) else {}
        baseline_scores = scores.get("baselines", {}) if isinstance(scores.get("baselines"), dict) else {}
        score_bits = ", ".join(
            f"{key}={baseline_scores.get(key)}" for key in sorted(baseline_scores.keys())
        )
        self.pr28_judge_status_var.set(
            f"PR28 judge: {judge_status} | {score_bits or 'scores unavailable'}"
        )
        promotion_payload = pr28_status.get("promotion", {})
        decision = promotion_payload.get("decision") or "-"
        reasons = promotion_payload.get("reasons", []) if isinstance(promotion_payload.get("reasons"), list) else []
        reason_text = ", ".join(str(item) for item in reasons) if reasons else "-"
        self.pr28_promotion_status_var.set(
            f"PR28 promotion: {decision} | reasons={reason_text}"
        )
        history_payload = pr28_status.get("history", {})
        history_path = history_payload.get("history_path") or history_payload.get("source", {}).get("path")
        self.pr28_evidence_var.set(
            "PR28 evidence: "
            f"{history_path or 'no history path'} | latest paths: "
            f"{PR28_TOURNAMENT_RESULT_LATEST_PATH}, {PR28_JUDGE_RESULT_LATEST_PATH}, {PR28_PROMOTION_DECISION_LATEST_PATH}"
        )

    def _refresh_policy_history(self) -> None:
        events_path = latest_events_file()
        entries = load_policy_history(POLICY_REGISTRY_PATH, events_path=events_path)
        self.policy_history_entries = entries
        latest_payload = load_policy_history_latest(LATEST_POLICY_HISTORY_PATH)
        source = latest_payload.get("source") if isinstance(latest_payload.get("source"), dict) else {}
        source_mode = source.get("mode", "unknown")
        source_path = source.get("path", "")
        last_decision = latest_payload.get("last_decision") if isinstance(latest_payload, dict) else {}
        decision_ts = ""
        decision_value = ""
        if isinstance(last_decision, dict):
            decision_ts = str(last_decision.get("ts_utc") or "")
            decision_value = str(last_decision.get("decision") or "")
        policy_version = latest_payload.get("policy_version") if isinstance(latest_payload, dict) else None
        if latest_payload.get("status") == "missing":
            missing_artifacts = latest_payload.get("missing_artifacts", [])
            searched = latest_payload.get("searched_paths", [])
            next_actions = latest_payload.get("suggested_next_actions", [])
            missing_text = ", ".join(str(item) for item in missing_artifacts) if missing_artifacts else "-"
            searched_text = ", ".join(str(item) for item in searched) if searched else "-"
            next_text = ", ".join(str(item) for item in next_actions) if next_actions else "-"
            self.policy_history_status_var.set(
                "Policy History: latest artifact missing | "
                f"source={source_mode} {source_path} | missing={missing_text} | looked={searched_text} | next={next_text}"
            )
        else:
            self.policy_history_status_var.set(
                f"Policy History: current={policy_version or 'unknown'} | last decision={decision_value or 'unknown'} @ {decision_ts or '-'} | "
                f"source={source_mode} {source_path}"
            )
        if hasattr(self, "policy_tree"):
            self.policy_tree.delete(*self.policy_tree.get_children())
            for entry in entries:
                self.policy_tree.insert(
                    "",
                    tk.END,
                    values=(
                        entry.get("ts_utc", ""),
                        entry.get("policy_version", ""),
                        entry.get("decision", ""),
                        entry.get("reason", ""),
                    ),
                )

    def _refresh_replay_panel(self) -> None:
        start = time.perf_counter()
        payload = load_replay_index_latest()
        elapsed = time.perf_counter() - start
        slow_note = f" | load_slow={elapsed:.2f}s" if elapsed > 0.5 else ""
        self._replay_index_payload = payload
        if payload.get("status") == "missing":
            reason = payload.get("missing_reason")
            searched = payload.get("searched_paths", [])
            searched_text = ", ".join(str(item) for item in searched) if searched else "-"
            self.replay_status_var.set(f"Replay: missing ({reason}) | looked={searched_text}")
            self.replay_detail_var.set(f"Latest replay: Missing/Not available{slow_note}")
            self.replay_latest_path_var.set("decision_cards_latest.jsonl: -")
            self._replay_cards = []
            self._apply_replay_filters()
            return
        missing_reason = payload.get("missing_reason")
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        source_path = source.get("path", "")
        if missing_reason == "replay_index_schema_mismatch":
            self.replay_status_var.set(
                f"Replay: Unsupported schema_version | source={source_path}"
            )
            self.replay_detail_var.set(f"Latest replay: Unsupported schema_version{slow_note}")
            self._replay_cards = []
            self._apply_replay_filters()
            return

        counts = payload.get("counts", {}) if isinstance(payload.get("counts"), dict) else {}
        num_cards = counts.get("num_cards", 0)
        created = payload.get("created_ts_utc") or payload.get("ts_utc") or "-"
        self.replay_status_var.set(f"Replay: loaded | source={source_path}")
        self.replay_detail_var.set(f"Latest replay: {created} | cards={num_cards}{slow_note}")

        pointers = payload.get("pointers", {}) if isinstance(payload.get("pointers"), dict) else {}
        decision_rel = str(pointers.get("decision_cards") or "")
        self.replay_latest_path_var.set(
            f"decision_cards_latest.jsonl: {decision_rel or '-'}"
        )
        decision_path = ROOT / decision_rel if decision_rel else None
        if decision_path and decision_path.exists():
            self._replay_cards = load_decision_cards(decision_path)
        else:
            self._replay_cards = []
        self._apply_replay_filters()

    def _apply_replay_filters(self) -> None:
        action_filter = self.replay_action_filter_var.get()
        symbol_filter = self.replay_symbol_filter_var.get().strip().lower()
        reject_only = self.replay_reject_only_var.get()
        guard_failed = self.replay_guard_failed_var.get()

        filtered: list[dict[str, object]] = []
        for card in self._replay_cards:
            if not isinstance(card, dict):
                continue
            action = str(card.get("action") or "")
            if action_filter != "(all)" and action != action_filter:
                continue
            symbol = str(card.get("symbol") or "").lower()
            if symbol_filter and symbol_filter not in symbol:
                continue
            decision = card.get("decision", {}) if isinstance(card.get("decision"), dict) else {}
            reject_codes = decision.get("reject_reason_codes", [])
            has_reject = bool(reject_codes) or action == "REJECT"
            if reject_only and not has_reject:
                continue
            guards = card.get("guards", {}) if isinstance(card.get("guards"), dict) else {}
            data_health = str(guards.get("data_health") or "")
            guard_failed_state = False
            if data_health and data_health.upper() != "PASS":
                guard_failed_state = True
            if guards.get("kill_switch") or not guards.get("cooldown_ok", True):
                guard_failed_state = True
            if not guards.get("limits_ok", True) or not guards.get("no_lookahead_ok", True):
                guard_failed_state = True
            if guard_failed and not guard_failed_state:
                continue
            filtered.append(card)

        self._replay_filtered_cards = filtered
        if hasattr(self, "replay_tree"):
            self.replay_tree.delete(*self.replay_tree.get_children())
            for card in filtered:
                decision = card.get("decision", {}) if isinstance(card.get("decision"), dict) else {}
                reject_codes = decision.get("reject_reason_codes", [])
                reject_text = ",".join(str(item) for item in reject_codes) if reject_codes else ""
                self.replay_tree.insert(
                    "",
                    tk.END,
                    values=(
                        card.get("ts_utc", ""),
                        card.get("step_id", ""),
                        card.get("symbol", ""),
                        card.get("action", ""),
                        decision.get("accepted", False),
                        reject_text,
                    ),
                )

    def _on_replay_select(self, _event: object = None) -> None:
        if not hasattr(self, "replay_tree"):
            return
        selection = self.replay_tree.selection()
        if not selection:
            self.replay_selected_detail_var.set("Selected card: (none)")
            if hasattr(self, "replay_detail_text"):
                self.replay_detail_text.configure(state=tk.NORMAL)
                self.replay_detail_text.delete("1.0", tk.END)
                self.replay_detail_text.configure(state=tk.DISABLED)
            return
        index = self.replay_tree.index(selection[0])
        if index >= len(self._replay_filtered_cards):
            return
        card = self._replay_filtered_cards[index]
        self.replay_selected_detail_var.set(
            f"Selected card: {card.get('action')} {card.get('symbol')} step={card.get('step_id')}"
        )
        if hasattr(self, "replay_detail_text"):
            self.replay_detail_text.configure(state=tk.NORMAL)
            self.replay_detail_text.delete("1.0", tk.END)
            self.replay_detail_text.insert(
                tk.END, json.dumps(card, ensure_ascii=False, indent=2)
            )
            self.replay_detail_text.configure(state=tk.DISABLED)

    def _open_replay_latest_file(self) -> None:
        payload = self._replay_index_payload or {}
        pointers = payload.get("pointers", {}) if isinstance(payload.get("pointers"), dict) else {}
        decision_rel = str(pointers.get("decision_cards") or "")
        if not decision_rel:
            messagebox.showinfo("Replay", "No replay file available")
            return
        path = ROOT / decision_rel
        if not path.exists():
            messagebox.showinfo("Replay", f"Replay file not found: {decision_rel}")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
                return
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)], env=_utf8_env())
                return
            subprocess.Popen(["xdg-open", str(path)], env=_utf8_env())
        except Exception:  # pragma: no cover - UI feedback
            self._show_copyable_text("Replay", "Copy the path below:", decision_rel)

    def _copy_replay_latest_path(self) -> None:
        payload = self._replay_index_payload or {}
        pointers = payload.get("pointers", {}) if isinstance(payload.get("pointers"), dict) else {}
        decision_rel = str(pointers.get("decision_cards") or "")
        if not decision_rel:
            messagebox.showinfo("Replay", "No replay file available")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(decision_rel)
            messagebox.showinfo("Replay", "Replay path copied to clipboard.")
        except Exception:  # pragma: no cover - UI feedback
            self._show_copyable_text("Replay", "Copy the path below:", decision_rel)

    def _copy_replay_evidence(self) -> None:
        if not hasattr(self, "replay_tree"):
            return
        selection = self.replay_tree.selection()
        if not selection:
            messagebox.showinfo("Replay", "No replay card selected")
            return
        index = self.replay_tree.index(selection[0])
        if index >= len(self._replay_filtered_cards):
            return
        card = self._replay_filtered_cards[index]
        evidence = card.get("evidence", {}) if isinstance(card.get("evidence"), dict) else {}
        paths = evidence.get("paths", []) if isinstance(evidence.get("paths"), list) else []
        payload = "\n".join(str(item) for item in paths) if paths else ""
        if not payload:
            messagebox.showinfo("Replay", "No evidence paths for selected card")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            messagebox.showinfo("Replay", "Evidence paths copied to clipboard.")
        except Exception:  # pragma: no cover - UI feedback
            self._show_copyable_text("Replay", "Copy the paths below:", payload)

    def _open_policy_evidence(self) -> None:
        if not hasattr(self, "policy_tree"):
            return
        selection = self.policy_tree.selection()
        if not selection:
            messagebox.showinfo("Policy History", "No policy history selected")
            return
        index = self.policy_tree.index(selection[0])
        if index >= len(self.policy_history_entries):
            messagebox.showinfo("Policy History", "Selected entry missing")
            return
        entry = self.policy_history_entries[index]
        evidence = entry.get("evidence")
        if not evidence:
            messagebox.showinfo("Policy History", "No evidence path for selection")
            return
        path = Path(str(evidence))
        if path.exists():
            try:
                if hasattr(os, "startfile"):
                    os.startfile(str(path))  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["xdg-open", str(path)], env=_utf8_env())
            except Exception as exc:  # pragma: no cover - UI feedback
                messagebox.showerror("Policy History", f"Failed to open evidence: {exc}")
        else:
            messagebox.showinfo("Policy History", f"Evidence not found: {evidence}")

    def _load_dashboard(self) -> None:
        if not compute_health or not compute_event_rows:
            return
        try:
            minutes = int(self.filter_minutes_var.get()) if hasattr(self, "filter_minutes_var") else 60
        except Exception:
            minutes = 60

        status = load_latest_status(LOGS_DIR) if load_latest_status else None
        supervisor_state = {}
        if STATE_PATH.exists():
            try:
                supervisor_state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                supervisor_state = {}
        events = load_recent_events(LOGS_DIR, since_minutes=minutes) if load_recent_events else []
        self._events_cache = events

        health = compute_health(status, events, supervisor_state) if compute_health else {}
        self._render_health(health)
        self._apply_event_filters(refresh_only=True)
    
    def _render_health(self, health: dict) -> None:
        lights = health.get("lights", {}) if isinstance(health, dict) else {}
        color_map = {"green": "#16a34a", "yellow": "#eab308", "red": "#ef4444", "unknown": "#9ca3af"}
        for key, widgets in getattr(self, "light_widgets", {}).items():
            data = lights.get(key) or {}
            status = data.get("status", "unknown")
            widgets["color"].configure(bg=color_map.get(status, "#9ca3af"))
            widgets["text"].configure(text=f"{widgets['text'].cget('text').split(':')[0]}: {data.get('value', '?')}")
            widgets["threshold"].configure(text=f"Threshold: {data.get('threshold', '')}\nEvidence: {data.get('evidence', '')}")

        cards = health.get("cards", []) if isinstance(health, dict) else []
        for idx, widget in enumerate(self.card_widgets):
            if idx < len(cards):
                card = cards[idx]
                widget["label"].configure(text=card.get("label", "-"))
                widget["value"].configure(text=str(card.get("value", "-")))
                widget["source"].configure(text=str(card.get("source", "")))
            else:
                widget["label"].configure(text="-")
                widget["value"].configure(text="-")
                widget["source"].configure(text="")

        evidence_text = health.get("evidence", "") if isinstance(health, dict) else ""
        self.evidence_text.configure(state=tk.NORMAL)
        self.evidence_text.delete("1.0", tk.END)
        self.evidence_text.insert(tk.END, evidence_text or "No evidence available")
        self.evidence_text.configure(state=tk.DISABLED)

    def _apply_event_filters(self, refresh_only: bool = False) -> None:
        if not compute_event_rows:
            return
        events = list(self._events_cache)
        try:
            minutes = int(self.filter_minutes_var.get()) if hasattr(self, "filter_minutes_var") else 60
        except Exception:
            minutes = 60

        type_filter = self.filter_type_var.get() if hasattr(self, "filter_type_var") else "ALL"
        severity_filter = self.filter_severity_var.get() if hasattr(self, "filter_severity_var") else "ALL"
        symbol_filter = (self.filter_symbol_var.get() or "").upper() if hasattr(self, "filter_symbol_var") else ""
        text_filter = (self.filter_text_var.get() or "").lower() if hasattr(self, "filter_text_var") else ""

        filtered = []
        for ev in events:
            if minutes and ev.get("__ts"):
                delta = time.time() - ev.get("__ts").timestamp()
                if delta > minutes * 60:
                    continue
            if type_filter != "ALL" and str(ev.get("event_type") or "") != type_filter:
                continue
            if severity_filter != "ALL" and str(ev.get("severity") or "").lower() != severity_filter.lower():
                continue
            if symbol_filter and str(ev.get("symbol") or "").upper() != symbol_filter:
                continue
            if text_filter:
                text_blob = json.dumps(ev, ensure_ascii=False).lower()
                if text_filter not in text_blob:
                    continue
            filtered.append(ev)

        rows = compute_event_rows(filtered)
        self.events_tree.delete(*self.events_tree.get_children())
        self._events_rows.clear()
        for row in rows:
            item_id = self.events_tree.insert(
                "", tk.END, values=(row.get("ts_et"), row.get("event_type"), row.get("symbol"), row.get("severity"), row.get("key_metric"), row.get("message"))
            )
            self._events_rows[item_id] = row

        leaderboard_rows = compute_move_leaderboard(events) if compute_move_leaderboard else []
        self.leaderboard.delete(*self.leaderboard.get_children())
        for lb in leaderboard_rows:
            self.leaderboard.insert(
                "",
                tk.END,
                values=(lb.get("symbol"), lb.get("last_move_pct"), lb.get("move_count_60m"), lb.get("max_abs_move_60m")),
            )

        if not refresh_only and not rows:
            messagebox.showinfo("Events", "No events match the filters")

    def _on_leaderboard_select(self, _event=None) -> None:
        selection = self.leaderboard.selection()
        if not selection:
            return
        values = self.leaderboard.item(selection[0]).get("values") or []
        if values:
            self.filter_symbol_var.set(str(values[0]))
            self._apply_event_filters()

    def _show_event_details(self, _event=None) -> None:
        selection = self.events_tree.selection()
        if not selection:
            return
        row = self._events_rows.get(selection[0])
        if not row:
            return
        raw = row.get("raw") or {}
        pretty = json.dumps(raw, ensure_ascii=False, indent=2)
        explanation = row.get("key_metric") or ""
        text = pretty
        if explanation:
            text += f"\n\nMetrics: {explanation}"
        self.event_details.configure(state=tk.NORMAL)
        self.event_details.delete("1.0", tk.END)
        self.event_details.insert(tk.END, text)
        self.event_details.configure(state=tk.DISABLED)

    def _copy_event_evidence(self) -> None:
        selection = self.events_tree.selection()
        if not selection:
            messagebox.showinfo("Copy", "Select an event first")
            return
        row = self._events_rows.get(selection[0])
        if not row:
            messagebox.showinfo("Copy", "No event selected")
            return
        evidence = row.get("evidence") or ""
        raw = row.get("raw") or {}
        payload = f"Evidence: {evidence}\n{json.dumps(raw, ensure_ascii=False, indent=2)}"
        self.clipboard_clear()
        self.clipboard_append(payload)
        messagebox.showinfo("Copy", "Evidence copied")

    def _handle_generate_packet(self) -> None:
        question = self.question_var.get().strip()
        if not question:
            messagebox.showwarning("AI Q&A", "Please enter a question first")
            return
        self._log(f"Generating packet for question: {question}")
        threading.Thread(target=self._run_generate_packet, args=(question,), daemon=True).start()

    def _handle_import_answer(self) -> None:
        if self.last_packet_path is None:
            messagebox.showwarning("AI Q&A", "Generate a packet first")
            return
        answer = self.answer_text.get("1.0", tk.END).strip()
        if not answer:
            messagebox.showwarning("AI Q&A", "Paste an answer first")
            return
        strict = self.strict_var.get()
        self._log("Importing answer with strict mode %s" % ("ON" if strict else "OFF"))
        threading.Thread(
            target=self._run_import_answer,
            args=(self.last_packet_path, answer, strict),
            daemon=True,
        ).start()

    def _parse_packet_paths(self, stdout: str) -> tuple[Path | None, Path | None]:
        packet_path: Path | None = None
        evidence_path: Path | None = None
        for line in stdout.splitlines():
            if line.startswith("PACKET_PATH="):
                packet_path = Path(line.split("PACKET_PATH=", 1)[1].strip())
            elif line.startswith("EVIDENCE_PACK_PATH="):
                evidence_path = Path(line.split("EVIDENCE_PACK_PATH=", 1)[1].strip())
            elif line.startswith("OUTPUT_PACKET="):
                packet_path = Path(line.split("OUTPUT_PACKET=", 1)[1].strip())
            elif line.startswith("OUTPUT_EVIDENCE_PACK="):
                evidence_path = Path(line.split("OUTPUT_EVIDENCE_PACK=", 1)[1].strip())
            elif "AI packet:" in line:
                try:
                    packet_path = Path(line.split("AI packet:", 1)[1].strip())
                except Exception:
                    pass
            elif "Evidence pack:" in line:
                try:
                    evidence_path = Path(line.split("Evidence pack:", 1)[1].strip())
                except Exception:
                    pass
        return packet_path, evidence_path

    def _run_generate_packet(self, question: str) -> None:
        cmd = [sys.executable, str(QA_FLOW_SCRIPT), "--question", question]
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
        )
        output = proc.stdout or ""
        error_output = proc.stderr or ""
        self._qa_output_queue.put(output)
        if error_output:
            self._qa_output_queue.put(error_output)
        packet_path, evidence_path = self._parse_packet_paths(output)
        if proc.returncode != 0:
            message = f"qa_flow exited with {proc.returncode}"
            if error_output:
                message += f"\n{error_output}"
            self._qa_output_queue.put(message)
            return
        if packet_path:
            self.last_packet_path = packet_path
            self._enqueue_ui(lambda: self.packet_path_var.set(f"Last packet: {packet_path}"))
            self._enqueue_ui(lambda: self._log(f"Packet created at {packet_path}"))
        else:
            self._enqueue_ui(lambda: self._log("Packet path not detected; check logs"))
        if evidence_path:
            self._enqueue_ui(lambda: self._log(f"Evidence pack at {evidence_path}"))

    def _parse_import_output(self, stdout: str) -> tuple[Path | None, Path | None, str]:
        answer_path: Path | None = None
        events_path: Path | None = None
        quality_summary = ""
        for line in stdout.splitlines():
            if line.startswith("Saved answer to:"):
                try:
                    answer_path = Path(line.split(":", 1)[1].strip())
                except Exception:
                    pass
            elif line.startswith("Appended event to:"):
                try:
                    events_path = Path(line.split(":", 1)[1].strip())
                except Exception:
                    pass
            elif line.startswith("Quality:"):
                quality_summary = line
        return answer_path, events_path, quality_summary

    def _run_import_answer(self, packet_path: Path, answer: str, strict: bool) -> None:
        cmd = [
            sys.executable,
            str(CAPTURE_ANSWER_SCRIPT),
            "--packet",
            str(packet_path),
            "--answer-text",
            answer,
        ]
        if strict:
            cmd.append("--strict")
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
        )
        output = proc.stdout or ""
        error_output = proc.stderr or ""
        if output:
            self._qa_output_queue.put(output)
        if error_output:
            self._qa_output_queue.put(error_output)
        answer_path, events_path, quality_summary = self._parse_import_output(output)
        if answer_path:
            self.last_answer_path = answer_path
            self._enqueue_ui(lambda: self.answer_status_var.set(f"Last answer: {answer_path}"))
        if events_path:
            self._enqueue_ui(lambda: self._log(f"AI_ANSWER appended to {events_path}"))
        if quality_summary:
            self._enqueue_ui(lambda: self._log(quality_summary))

        if proc.returncode == 2:
            self._enqueue_ui(
                lambda: messagebox.showwarning(
                    "AI Q&A",
                    "Strict mode rejected the answer. Please ask ChatGPT to remove trade advice and add citations.",
                )
            )
        elif proc.returncode != 0:
            self._enqueue_ui(
                lambda: messagebox.showerror(
                    "AI Q&A",
                    f"capture_ai_answer failed with code {proc.returncode}\n{error_output}",
                )
            )
        else:
            self._enqueue_ui(lambda: self._log("Answer imported successfully"))

    def _copy_packet(self) -> None:
        if not self.last_packet_path or not self.last_packet_path.exists():
            messagebox.showwarning("AI Q&A", "No packet to copy yet")
            return
        content = self.last_packet_path.read_text(encoding="utf-8")
        self.clipboard_clear()
        self.clipboard_append(content)
        self._log("Packet copied to clipboard")

    def _copy_summary(self) -> None:
        summary = self.summary_text.get("1.0", tk.END).strip()
        if not summary:
            messagebox.showinfo("摘要", "暂无摘要内容")
            return
        self.clipboard_clear()
        self.clipboard_append(summary)
        messagebox.showinfo("摘要", "摘要已复制")

    def _open_output_folder(self) -> None:
        target = None
        if self.last_answer_path:
            target = self.last_answer_path.parent
        elif self.last_packet_path:
            target = self.last_packet_path.parent
        if target is None:
            messagebox.showinfo("AI Q&A", "No packet or answer yet")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(target)], env=_utf8_env())
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("AI Q&A", f"Failed to open folder: {exc}")

    def _drain_qa_output(self) -> None:
        while True:
            try:
                message = self._qa_output_queue.get_nowait()
            except queue.Empty:
                break
            self._log(message)
        self.after(500, self._drain_qa_output)

    def _drain_verify_output(self) -> None:
        while True:
            try:
                message = self._verify_output_queue.get_nowait()
            except queue.Empty:
                break
            self.verify_output.configure(state=tk.NORMAL)
            self.verify_output.insert(tk.END, message + "\n\n")
            self.verify_output.see(tk.END)
            self.verify_output.configure(state=tk.DISABLED)
        self.after(500, self._drain_verify_output)

    def _drain_training_output(self) -> None:
        while True:
            try:
                message = self._training_output_queue.get_nowait()
            except queue.Empty:
                break
            self.training_output.configure(state=tk.NORMAL)
            self.training_output.insert(tk.END, message + "\n\n")
            self.training_output.see(tk.END)
            self.training_output.configure(state=tk.DISABLED)
        self.after(600, self._drain_training_output)

    def _drain_summary_queue(self) -> None:
        updated = False
        while True:
            try:
                message = self._summary_queue.get_nowait()
            except queue.Empty:
                break
            self.summary_text.configure(state=tk.NORMAL)
            self.summary_text.delete("1.0", tk.END)
            self.summary_text.insert(tk.END, message)
            self.summary_text.configure(state=tk.DISABLED)
            updated = True
        if updated:
            self.summary_text.see(tk.END)
        self.after(1000, self._drain_summary_queue)

    def _start_auto_refresh(self) -> None:
        def loop() -> None:
            while True:
                time.sleep(1.5)
                self.after(0, self._refresh)

        threading.Thread(target=loop, daemon=True).start()

        def summary_loop() -> None:
            while True:
                time.sleep(2)
                if explain_now:
                    try:
                        summary = explain_now.generate_summary()
                    except Exception as exc:  # pragma: no cover - UI feedback
                        summary = f"无法生成摘要: {exc}"
                else:
                    summary = "摘要模块不可用"
                self._summary_queue.put(summary)

        threading.Thread(target=summary_loop, daemon=True).start()

    def _build_run_tab(self) -> None:
        top_frame = tk.Frame(self.run_tab)
        top_frame.pack(fill=tk.X, pady=5)

        tk.Button(top_frame, text="Start", command=self._handle_start).pack(
            side=tk.LEFT, padx=5
        )
        tk.Button(top_frame, text="Stop", command=self._handle_stop).pack(
            side=tk.LEFT, padx=5
        )
        tk.Button(
            top_frame,
            text="Clear Kill Switch (SIM-only)",
            command=self._handle_clear_kill_switch,
        ).pack(side=tk.LEFT, padx=5)

        self.run_log = ScrolledText(self.run_tab, height=10, wrap=tk.WORD)
        self.run_log.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.run_log.configure(state=tk.DISABLED)

        training_frame = tk.LabelFrame(self.run_tab, text="Training", padx=5, pady=5)
        training_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        hud_frame = tk.LabelFrame(training_frame, text="24/7 SIM HUD", padx=6, pady=6)
        hud_frame.pack(fill=tk.X, expand=False, padx=2, pady=4)

        hud_row = tk.Frame(hud_frame)
        hud_row.pack(fill=tk.X)

        self.hud_mode_lamp = tk.Label(
            hud_row, text="STOPPED", width=10, relief=tk.SOLID, fg="white", bg="#555"
        )
        self.hud_mode_lamp.pack(side=tk.LEFT, padx=4, pady=2)

        status_col = tk.Frame(hud_row)
        status_col.pack(side=tk.LEFT, padx=6)
        tk.Label(status_col, textvariable=self.hud_mode_detail_var, anchor="w").pack(anchor="w")
        tk.Label(status_col, textvariable=self.hud_kill_switch_var, anchor="w").pack(anchor="w")
        tk.Label(status_col, textvariable=self.hud_kill_switch_detail_var, anchor="w").pack(anchor="w")
        tk.Label(status_col, textvariable=self.hud_kill_switch_trip_var, anchor="w").pack(anchor="w")
        self.hud_data_health_label = tk.Label(status_col, textvariable=self.hud_data_health_var, anchor="w")
        self.hud_data_health_label.pack(anchor="w")

        timing_col = tk.Frame(hud_row)
        timing_col.pack(side=tk.LEFT, padx=8)
        tk.Label(timing_col, textvariable=self.hud_stage_var, anchor="w").pack(anchor="w")
        tk.Label(timing_col, textvariable=self.hud_run_id_var, anchor="w").pack(anchor="w")
        tk.Label(timing_col, textvariable=self.hud_elapsed_var, anchor="w").pack(anchor="w")
        tk.Label(timing_col, textvariable=self.hud_next_iter_var, anchor="w").pack(anchor="w")

        budget_col = tk.Frame(hud_row)
        budget_col.pack(side=tk.LEFT, padx=8)
        tk.Label(budget_col, textvariable=self.hud_budget_iter_var, anchor="w").pack(anchor="w")
        tk.Label(budget_col, textvariable=self.hud_budget_hour_var, anchor="w").pack(anchor="w")
        tk.Label(budget_col, textvariable=self.hud_budget_disk_var, anchor="w").pack(anchor="w")

        risk_frame = tk.Frame(hud_frame)
        risk_frame.pack(fill=tk.X, pady=4)
        tk.Label(risk_frame, text="Risk-first KPIs", font=("Arial", 10, "bold"), fg="#8B0000").pack(anchor="w")
        tk.Label(risk_frame, textvariable=self.hud_max_dd_var, font=("Arial", 12, "bold"), fg="#8B0000").pack(anchor="w")
        tk.Label(risk_frame, textvariable=self.hud_turnover_var, font=("Arial", 11)).pack(anchor="w")
        tk.Label(risk_frame, textvariable=self.hud_rejects_var, font=("Arial", 11)).pack(anchor="w")
        tk.Label(risk_frame, textvariable=self.hud_gates_var, font=("Arial", 11)).pack(anchor="w")
        tk.Label(risk_frame, textvariable=self.hud_equity_var, font=("Arial", 11), fg="#005f73").pack(anchor="w")

        controls = tk.Frame(training_frame)
        controls.pack(fill=tk.X, pady=2)

        tk.Label(controls, text="Max runtime (s):").pack(side=tk.LEFT)
        self.training_runtime_var = tk.StringVar(value="60")
        tk.Entry(controls, textvariable=self.training_runtime_var, width=8).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(
            controls,
            text="Start Nightly Training (SIM-only)",
            command=self._handle_start_training,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            controls,
            text="Nightly (8h) Preset",
            command=self._handle_start_nightly,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            controls,
            text="Show Latest Training Summary",
            command=self._handle_show_latest_training_summary,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            controls,
            text="Open Latest Run Folder",
            command=self._handle_open_latest_run_folder,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            controls,
            text="Tail Recent Events",
            command=self._handle_tail_events,
        ).pack(side=tk.LEFT, padx=5)

        info_frame = tk.Frame(training_frame)
        info_frame.pack(fill=tk.X, pady=2)
        self.training_status_var = tk.StringVar(value="Status: idle")
        self.training_run_dir_var = tk.StringVar(value="RUN_DIR: (none)")
        self.training_summary_path_var = tk.StringVar(value="SUMMARY_PATH: (none)")
        tk.Label(info_frame, textvariable=self.training_status_var, anchor="w").pack(
            anchor="w"
        )
        tk.Label(info_frame, textvariable=self.training_run_dir_var, anchor="w").pack(
            anchor="w"
        )
        tk.Label(
            info_frame, textvariable=self.training_summary_path_var, anchor="w"
        ).pack(anchor="w")

        retention_frame = tk.Frame(training_frame)
        retention_frame.pack(fill=tk.X, pady=2)
        self.retain_days_var = tk.StringVar(value="7")
        self.retain_latest_n_var = tk.StringVar(value="50")
        self.retain_total_mb_var = tk.StringVar(value="5000")
        tk.Label(retention_frame, text="retain-days").pack(side=tk.LEFT)
        tk.Entry(retention_frame, textvariable=self.retain_days_var, width=6).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(retention_frame, text="retain-latest-n").pack(side=tk.LEFT)
        tk.Entry(retention_frame, textvariable=self.retain_latest_n_var, width=8).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(retention_frame, text="max-total-train-runs-mb").pack(side=tk.LEFT)
        tk.Entry(retention_frame, textvariable=self.retain_total_mb_var, width=10).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(retention_frame, text="(Nightly preset会强制携带保留参数)", fg="gray").pack(
            side=tk.LEFT, padx=6
        )

        service_frame = tk.LabelFrame(training_frame, text="24/7 Service", padx=4, pady=4)
        service_frame.pack(fill=tk.X, expand=False, padx=2, pady=4)

        service_controls = tk.Frame(service_frame)
        service_controls.pack(fill=tk.X, pady=2)
        tk.Button(service_controls, text="Start 24/7 Service", command=self._handle_start_service).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(service_controls, text="Stop Service", command=self._handle_stop_service).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(
            service_controls,
            text="Show Rolling Summary",
            command=self._handle_show_rolling_summary,
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            service_controls,
            text="Open Latest Run Folder",
            command=self._handle_open_latest_service_run,
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            service_controls,
            text="Open Latest Summary",
            command=self._handle_open_latest_service_summary,
        ).pack(side=tk.LEFT, padx=4)

        preset_frame = tk.Frame(service_frame)
        preset_frame.pack(fill=tk.X, pady=2)
        tk.Label(preset_frame, text="Cadence preset").pack(side=tk.LEFT)
        ttk.OptionMenu(
            preset_frame,
            self.service_cadence_preset_var,
            self.service_cadence_preset_var.get(),
            *CADENCE_LABELS.keys(),
            command=lambda *_: self._apply_cadence_preset_fields(
                CADENCE_LABELS.get(self.service_cadence_preset_var.get(), "micro")
            ),
        ).pack(side=tk.LEFT, padx=4)

        service_limits = tk.Frame(service_frame)
        service_limits.pack(fill=tk.X, pady=2)
        tk.Label(service_limits, text="episode-seconds").pack(side=tk.LEFT)
        tk.Entry(service_limits, textvariable=self.service_episode_seconds_var, width=6).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(service_limits, text="max-episodes-per-hour").pack(side=tk.LEFT)
        tk.Entry(service_limits, textvariable=self.service_max_hour_var, width=6).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(service_limits, text="max-episodes-per-day").pack(side=tk.LEFT)
        tk.Entry(service_limits, textvariable=self.service_max_day_var, width=6).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(service_limits, text="cooldown-seconds-between-episodes").pack(side=tk.LEFT)
        tk.Entry(service_limits, textvariable=self.service_cooldown_var, width=6).pack(
            side=tk.LEFT, padx=3
        )

        service_budget_row = tk.Frame(service_frame)
        service_budget_row.pack(fill=tk.X, pady=2)
        tk.Label(service_budget_row, text="max-steps").pack(side=tk.LEFT)
        tk.Entry(service_budget_row, textvariable=self.service_max_steps_var, width=7).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(service_budget_row, text="max-trades").pack(side=tk.LEFT)
        tk.Entry(service_budget_row, textvariable=self.service_max_trades_var, width=7).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(service_budget_row, text="max-events-per-hour").pack(side=tk.LEFT)
        tk.Entry(service_budget_row, textvariable=self.service_max_events_var, width=7).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(service_budget_row, text="max-disk-mb").pack(side=tk.LEFT)
        tk.Entry(service_budget_row, textvariable=self.service_max_disk_var, width=7).pack(
            side=tk.LEFT, padx=3
        )
        tk.Label(service_budget_row, text="max-runtime-per-day").pack(side=tk.LEFT)
        tk.Entry(service_budget_row, textvariable=self.service_max_runtime_day_var, width=8).pack(
            side=tk.LEFT, padx=3
        )

        service_status_frame = tk.Frame(service_frame)
        service_status_frame.pack(fill=tk.X, pady=2)
        tk.Label(service_status_frame, textvariable=self.service_status_var, anchor="w").pack(
            anchor="w"
        )
        tk.Label(service_status_frame, textvariable=self.service_run_dir_var, anchor="w").pack(
            anchor="w"
        )
        tk.Label(service_status_frame, textvariable=self.service_summary_var, anchor="w").pack(
            anchor="w"
        )

        tk.Label(training_frame, text="Training Output:").pack(anchor="w")
        self.training_output = ScrolledText(training_frame, height=8, wrap=tk.WORD)
        self.training_output.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.training_output.configure(state=tk.DISABLED)

        tk.Label(training_frame, text="Latest Summary:").pack(anchor="w")
        self.training_summary_text = ScrolledText(training_frame, height=6, wrap=tk.WORD)
        self.training_summary_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.training_summary_text.configure(state=tk.DISABLED)

        wakeup_frame = tk.LabelFrame(training_frame, text="Wake-up Dashboard", padx=5, pady=5)
        wakeup_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=4)

        wakeup_top = tk.Frame(wakeup_frame)
        wakeup_top.pack(fill=tk.X, pady=2)
        tk.Button(wakeup_top, text="Refresh", command=self._refresh_wakeup_dashboard).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(
            wakeup_top,
            text="Open Latest Run Folder",
            command=self._handle_open_latest_wakeup_run,
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            wakeup_top,
            text="Open Latest Summary",
            command=self._handle_open_latest_wakeup_summary,
        ).pack(side=tk.LEFT, padx=4)

        self.wakeup_run_dir_var = tk.StringVar(value="latest_run_dir: (none)")
        self.wakeup_summary_path_var = tk.StringVar(value="summary_path: (none)")
        self.wakeup_stop_reason_var = tk.StringVar(value="stop_reason: (unknown)")
        self.wakeup_net_change_var = tk.StringVar(value="net_change: (unknown)")
        self.wakeup_max_drawdown_var = tk.StringVar(value="max_drawdown: (unknown)")
        self.wakeup_trades_var = tk.StringVar(value="trades_count: (unknown)")
        self.wakeup_rejects_var = tk.StringVar(value="reject_reasons_top3: (unknown)")
        self.wakeup_warning_var = tk.StringVar(value="")

        for var in [
            self.wakeup_run_dir_var,
            self.wakeup_summary_path_var,
            self.wakeup_stop_reason_var,
            self.wakeup_net_change_var,
            self.wakeup_max_drawdown_var,
            self.wakeup_trades_var,
            self.wakeup_rejects_var,
            self.wakeup_warning_var,
        ]:
            tk.Label(wakeup_frame, textvariable=var, anchor="w").pack(anchor="w")

        tk.Label(wakeup_frame, text="Summary preview:").pack(anchor="w")
        self.wakeup_summary_preview = ScrolledText(wakeup_frame, height=8, wrap=tk.WORD)
        self.wakeup_summary_preview.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.wakeup_summary_preview.configure(state=tk.DISABLED)
        self._refresh_wakeup_dashboard()

    def _build_action_center_tab(self) -> None:
        status_frame = tk.LabelFrame(self.action_center_tab, text="Status", padx=6, pady=6)
        status_frame.pack(fill=tk.X, padx=6, pady=6)
        tk.Label(status_frame, textvariable=self.action_center_status_marker_var, anchor="w").pack(
            side=tk.LEFT, padx=6
        )
        tk.Label(status_frame, textvariable=self.action_center_doctor_status_var, anchor="w").pack(
            side=tk.LEFT, padx=6
        )
        tk.Label(status_frame, textvariable=self.action_center_data_health_var, anchor="w").pack(side=tk.LEFT, padx=6)
        tk.Label(status_frame, textvariable=self.action_center_last_report_var, anchor="w").pack(side=tk.LEFT, padx=6)
        tk.Label(status_frame, textvariable=self.action_center_last_doctor_var, anchor="w").pack(
            side=tk.LEFT, padx=6
        )
        tk.Label(status_frame, textvariable=self.action_center_last_apply_var, anchor="w").pack(side=tk.LEFT, padx=6)

        controls = tk.Frame(self.action_center_tab)
        controls.pack(fill=tk.X, padx=6, pady=4)
        tk.Button(controls, text="Run Doctor", command=self._run_doctor_report).pack(side=tk.LEFT, padx=4)
        tk.Button(controls, text="Refresh Actions", command=self._generate_action_center_report).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(controls, text="Fix All (Safe)", command=self._apply_safe_actions).pack(side=tk.LEFT, padx=4)
        tk.Button(controls, text="Open Evidence Pack Folder", command=self._open_evidence_pack_folder).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(controls, text="Copy Apply Command", command=self._copy_action_center_apply_command).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(controls, text="Apply Selected Action", command=self._apply_selected_action).pack(
            side=tk.LEFT, padx=4
        )
        tk.Checkbutton(
            controls,
            text="Show Advanced",
            variable=self.action_center_show_advanced_var,
            command=lambda: self._render_action_center_actions(self._action_center_actions),
        ).pack(side=tk.LEFT, padx=4)

        evidence_frame = tk.Frame(self.action_center_tab)
        evidence_frame.pack(fill=tk.X, padx=6, pady=(0, 4))
        tk.Label(evidence_frame, text="Evidence path:", anchor="w").pack(side=tk.LEFT, padx=4)
        evidence_entry = tk.Entry(evidence_frame, textvariable=self.action_center_evidence_path_var, width=60)
        evidence_entry.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
        evidence_entry.configure(state="readonly")
        tk.Button(evidence_frame, text="Copy Path", command=self._copy_action_center_evidence_path).pack(
            side=tk.LEFT, padx=4
        )

        list_frame = tk.LabelFrame(self.action_center_tab, text="Actions", padx=6, pady=6)
        list_frame.pack(fill=tk.BOTH, padx=6, pady=4, expand=True)
        columns = ("id", "severity", "risk", "summary", "evidence", "last_seen")
        self.action_center_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=8)
        self.action_center_tree.heading("id", text="ID")
        self.action_center_tree.heading("severity", text="Severity")
        self.action_center_tree.heading("risk", text="Risk Level")
        self.action_center_tree.heading("summary", text="Summary")
        self.action_center_tree.heading("evidence", text="Evidence Paths")
        self.action_center_tree.heading("last_seen", text="Last Seen (UTC)")
        self.action_center_tree.column("id", width=180, anchor="w")
        self.action_center_tree.column("severity", width=80, anchor="w")
        self.action_center_tree.column("risk", width=90, anchor="w")
        self.action_center_tree.column("summary", width=280, anchor="w")
        self.action_center_tree.column("evidence", width=420, anchor="w")
        self.action_center_tree.column("last_seen", width=180, anchor="w")
        self.action_center_tree.pack(fill=tk.BOTH, expand=True)
        self.action_center_tree.bind("<<TreeviewSelect>>", self._on_action_center_select)
        tk.Label(list_frame, textvariable=self.action_center_selected_var, anchor="w").pack(
            anchor="w", pady=(4, 0)
        )

        doctor_frame = tk.LabelFrame(self.action_center_tab, text="Doctor", padx=6, pady=6)
        doctor_frame.pack(fill=tk.BOTH, padx=6, pady=4)
        self.action_center_doctor_box = ScrolledText(doctor_frame, height=6, wrap=tk.WORD)
        self.action_center_doctor_box.pack(fill=tk.BOTH, expand=True)
        self.action_center_doctor_box.configure(state=tk.DISABLED)

        self._refresh_action_center_report()
        self._refresh_action_center_doctor()

    def _build_health_tab(self) -> None:
        container = tk.Frame(self.health_tab)
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        lights_frame = tk.LabelFrame(container, text="Health Lights", padx=5, pady=5)
        lights_frame.pack(fill=tk.X)
        self.light_widgets = {}
        for key, label in [
            ("data_fresh", "Data Fresh"),
            ("data_flat", "Data Flat"),
            ("system_alive", "System Alive"),
        ]:
            frame = tk.Frame(lights_frame, padx=5, pady=3)
            frame.pack(side=tk.LEFT, padx=5)
            color = tk.Label(frame, text="  ", width=2, relief=tk.RIDGE)
            color.pack(side=tk.LEFT)
            text = tk.Label(frame, text=f"{label}: ?")
            text.pack(side=tk.LEFT, padx=3)
            threshold = tk.Label(frame, text="")
            threshold.pack(anchor="w")
            self.light_widgets[key] = {"color": color, "text": text, "threshold": threshold}

        cards_frame = tk.LabelFrame(container, text="Key Numbers", padx=5, pady=5)
        cards_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.card_widgets: List[dict[str, tk.Label]] = []
        for _ in range(6):
            holder = tk.Frame(cards_frame, padx=5, pady=5, relief=tk.GROOVE, borderwidth=1)
            holder.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3, pady=3)
            label = tk.Label(holder, text="Label", font=("Arial", 10, "bold"))
            label.pack(anchor="w")
            value = tk.Label(holder, text="-", font=("Arial", 14))
            value.pack(anchor="w")
            source = tk.Label(holder, text="source", fg="gray")
            source.pack(anchor="w")
            self.card_widgets.append({"label": label, "value": value, "source": source})

        evidence_frame = tk.LabelFrame(container, text="Evidence", padx=5, pady=5)
        evidence_frame.pack(fill=tk.BOTH, expand=True)
        self.evidence_text = ScrolledText(evidence_frame, height=6, wrap=tk.WORD)
        self.evidence_text.pack(fill=tk.BOTH, expand=True)
        self.evidence_text.configure(state=tk.DISABLED)

    def _build_events_tab(self) -> None:
        top_frame = tk.Frame(self.events_tab)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        filter_frame = tk.LabelFrame(top_frame, text="Filters", padx=5, pady=5)
        filter_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        tk.Label(filter_frame, text="Last N minutes").grid(row=0, column=0, sticky="w")
        self.filter_minutes_var = tk.StringVar(value="60")
        tk.Entry(filter_frame, textvariable=self.filter_minutes_var, width=8).grid(row=0, column=1, padx=4)

        tk.Label(filter_frame, text="Symbol").grid(row=0, column=2, sticky="w")
        self.filter_symbol_var = tk.StringVar()
        tk.Entry(filter_frame, textvariable=self.filter_symbol_var, width=10).grid(row=0, column=3, padx=4)

        tk.Label(filter_frame, text="Type").grid(row=1, column=0, sticky="w")
        self.filter_type_var = tk.StringVar(value="ALL")
        type_options = ["ALL", "MOVE", "DATA_STALE", "DATA_MISSING", "DATA_FLAT", "AI_ANSWER", "ALERTS_START"]
        ttk.Combobox(filter_frame, textvariable=self.filter_type_var, values=type_options, width=12, state="readonly").grid(row=1, column=1, padx=4)

        tk.Label(filter_frame, text="Severity").grid(row=1, column=2, sticky="w")
        self.filter_severity_var = tk.StringVar(value="ALL")
        severity_options = ["ALL", "low", "medium", "high"]
        ttk.Combobox(
            filter_frame, textvariable=self.filter_severity_var, values=severity_options, width=12, state="readonly"
        ).grid(row=1, column=3, padx=4)

        tk.Label(filter_frame, text="Text contains").grid(row=2, column=0, sticky="w")
        self.filter_text_var = tk.StringVar()
        tk.Entry(filter_frame, textvariable=self.filter_text_var, width=20).grid(row=2, column=1, columnspan=2, sticky="we")

        tk.Button(filter_frame, text="Apply", command=self._apply_event_filters).grid(row=2, column=3, padx=4)

        radar_frame = tk.LabelFrame(top_frame, text="MOVE Leaderboard (60m)", padx=5, pady=5)
        radar_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        columns = ("symbol", "last_move_pct", "move_count", "max_abs")
        self.leaderboard = ttk.Treeview(radar_frame, columns=columns, show="headings", height=5)
        for col, width, text in [
            ("symbol", 80, "Symbol"),
            ("last_move_pct", 120, "Last move %"),
            ("move_count", 120, "Count 60m"),
            ("max_abs", 150, "Max |move| 60m"),
        ]:
            self.leaderboard.heading(col, text=text)
            self.leaderboard.column(col, width=width, anchor="center")
        self.leaderboard.pack(fill=tk.BOTH, expand=True)
        self.leaderboard.bind("<<TreeviewSelect>>", self._on_leaderboard_select)

        table_frame = tk.Frame(self.events_tab)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ("ts", "event_type", "symbol", "severity", "key_metric", "message")
        self.events_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for col, width in [
            ("ts", 90),
            ("event_type", 100),
            ("symbol", 80),
            ("severity", 80),
            ("key_metric", 160),
            ("message", 320),
        ]:
            self.events_tree.heading(col, text=col)
            self.events_tree.column(col, width=width, anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.events_tree.yview)
        self.events_tree.configure(yscrollcommand=vsb.set)
        self.events_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.events_tree.bind("<Double-1>", self._show_event_details)

        details_frame = tk.LabelFrame(self.events_tab, text="Details", padx=5, pady=5)
        details_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.event_details = ScrolledText(details_frame, height=8, wrap=tk.WORD)
        self.event_details.pack(fill=tk.BOTH, expand=True)
        self.event_details.configure(state=tk.DISABLED)
        btn_frame = tk.Frame(details_frame)
        btn_frame.pack(fill=tk.X, pady=3)
        tk.Button(btn_frame, text="Details", command=self._show_event_details).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Copy Evidence", command=self._copy_event_evidence).pack(side=tk.LEFT, padx=5)

    def _build_summary_tab(self) -> None:
        summary_frame = tk.Frame(self.summary_tab)
        summary_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        button_frame = tk.Frame(summary_frame)
        button_frame.pack(fill=tk.X, pady=2)
        tk.Button(button_frame, text="复制摘要", command=self._copy_summary).pack(
            side=tk.RIGHT, padx=5
        )
        self.summary_text = ScrolledText(summary_frame, wrap=tk.WORD)
        self.summary_text.pack(fill=tk.BOTH, expand=True)
        self.summary_text.configure(state=tk.DISABLED)

    def _build_verify_tab(self) -> None:
        button_frame = tk.Frame(self.verify_tab)
        button_frame.pack(fill=tk.X, pady=5)
        tk.Button(
            button_frame,
            text="verify_smoke",
            command=lambda: self._run_tool("verify_smoke.py"),
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            button_frame,
            text="verify_e2e_qa_loop",
            command=lambda: self._run_tool("verify_e2e_qa_loop.py"),
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            button_frame,
            text="verify_ui_actions",
            command=lambda: self._run_tool("verify_ui_actions.py"),
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            button_frame,
            text="Foundation Gates",
            command=lambda: self._run_tool("verify_foundation.py"),
        ).pack(side=tk.LEFT, padx=5)

        self.verify_output = ScrolledText(self.verify_tab, wrap=tk.WORD)
        self.verify_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.verify_output.configure(state=tk.DISABLED)


def main() -> int:
    if configure_stdio_utf8:
        try:
            configure_stdio_utf8()
        except Exception:
            pass
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
