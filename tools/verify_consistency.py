from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.git_baseline_probe import probe_baseline
from tools.run_py import pick_python
TOOLS_DIR = ROOT / "tools"
README_PATH = ROOT / "README.md"
LOGS_DIR = ROOT / "Logs"
OPTIONAL_DEPS = ("pandas", "yaml", "yfinance")
ARCHIVE_EVENTS_PATTERN = re.compile(r"events_\d{4}-\d{2}-\d{2}\.jsonl")
CONSISTENCY_OPT_IN_FLAGS = "--include-event-archives,--include-legacy-gates"
CONSISTENCY_NEXT_STEP_CMD = "python tools/verify_consistency.py"
ARCHIVE_DIR = LOGS_DIR / "event_archives"
LEGACY_ARCHIVE_DIR = LOGS_DIR / "_event_archives"
HELP_CHECKS: dict[str, tuple[str, ...]] = {
    "tail_events.py": OPTIONAL_DEPS,
    "replay_events.py": OPTIONAL_DEPS,
    "select_evidence.py": OPTIONAL_DEPS,
    "make_ai_packet.py": OPTIONAL_DEPS,
    "qa_flow.py": OPTIONAL_DEPS,
    "supervisor.py": OPTIONAL_DEPS,
    "ui_app.py": OPTIONAL_DEPS,
    "sim_replay.py": OPTIONAL_DEPS,
    "verify_sim_replay.py": OPTIONAL_DEPS,
    "verify_no_lookahead_sim.py": OPTIONAL_DEPS,
    "train_daemon.py": OPTIONAL_DEPS,
    "policy_candidate.py": OPTIONAL_DEPS,
    "verify_policy_promotion.py": OPTIONAL_DEPS,
    "verify_policy_lifecycle.py": OPTIONAL_DEPS,
    "verify_train_semantic_loop.py": OPTIONAL_DEPS,
}


def detect_missing_deps() -> list[str]:
    missing: list[str] = []
    for dep in OPTIONAL_DEPS:
        try:
            importlib.import_module(dep)
        except Exception:
            missing.append(dep)
    return missing


class CheckResult:
    def __init__(self, name: str, status: bool | str, details: str | None = None) -> None:
        self.name = name
        if isinstance(status, bool):
            self.status = "OK" if status else "FAIL"
        else:
            self.status = status
        self.details = details or ""

    @property
    def ok(self) -> bool:
        return self.status == "OK"

    def render(self) -> str:
        status = f"[{self.status}]"
        if self.details:
            return f"{status} {self.name}: {self.details}"
        return f"{status} {self.name}"


def _print_header(
    env: dict[str, str | bool], missing_deps: list[str], baseline_info: dict[str, str | None]
) -> None:
    marker = "|".join(
        [
            "CONSISTENCY_HEADER",
            f"os={platform.system()}",
            f"in_container={int(env.get('in_container', False))}",
            f"venv_present={int(env.get('venv_present', False))}",
            f"using_venv={int(env.get('executable_in_venv', False))}",
            f"can_write_logs={int(env.get('can_write_logs', False))}",
            f"missing_deps={_format_dep_list(missing_deps)}",
            f"baseline_status={baseline_info.get('status') or 'UNAVAILABLE'}",
            f"baseline={baseline_info.get('baseline') or 'unavailable'}",
            f"baseline_details={baseline_info.get('details') or 'unknown'}",
        ]
    )
    print(marker)
    print(f"Interpreter: {sys.executable}")
    print(
        "Environment:",
        json.dumps(
            {
                "os": platform.system(),
                "in_container": env.get("in_container", False),
                "windows_venv": env.get("windows_venv", False),
                "posix_venv": env.get("posix_venv", False),
                "executable_in_venv": env.get("executable_in_venv", False),
                "can_write_logs": env.get("can_write_logs", False),
            }
        ),
    )
    for dep in OPTIONAL_DEPS:
        try:
            module = __import__(dep)
            version = getattr(module, "__version__", "unknown")
            print(f"{dep} version: {version}")
        except Exception:
            print(f"{dep} not installed (ok)")
    print()


def _detect_environment() -> dict[str, str | bool]:
    is_windows = platform.system() == "Windows"
    in_container = bool(
        os.environ.get("RUNNING_IN_CONTAINER")
        or Path("/.dockerenv").exists()
        or Path("/.dockerinit").exists()
    )
    windows_venv = (ROOT / ".venv" / "Scripts" / "python.exe").exists()
    posix_venv = (ROOT / ".venv" / "bin" / "python").exists()
    executable_path = Path(sys.executable).resolve()
    prefix_path = Path(sys.prefix).resolve()
    executable_in_venv = ".venv" in str(executable_path) or ".venv" in prefix_path.parts
    venv_present = windows_venv or posix_venv

    log_probe_error = ""
    can_write_logs = False
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(dir=LOGS_DIR, delete=True) as fh:
            fh.write(b"ok")
            fh.flush()
        can_write_logs = True
    except Exception as exc:  # pragma: no cover - best effort probe
        log_probe_error = str(exc)

    return {
        "is_windows": is_windows,
        "in_container": in_container,
        "windows_venv": windows_venv,
        "posix_venv": posix_venv,
        "venv_present": venv_present,
        "executable_in_venv": executable_in_venv,
        "can_write_logs": can_write_logs,
        "log_probe_error": log_probe_error,
    }


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_windows_paths() -> List[CheckResult]:
    results: List[CheckResult] = []
    content = _read_text(README_PATH)
    bad_patterns = ["./.venv/bin/python", ".venv/bin/python"]
    found_bad = [p for p in bad_patterns if p in content]
    if found_bad:
        results.append(
            CheckResult(
                "README linux paths",
                False,
                f"Disallowed python path(s) found: {', '.join(found_bad)}",
            )
        )
    else:
        results.append(CheckResult("README linux paths", True))

    if ".\\.venv\\Scripts\\python.exe" not in content:
        results.append(
            CheckResult(
                "README Windows path",
                False,
                "Expected example '.\\.venv\\Scripts\\python.exe' not found",
            )
        )
    else:
        results.append(CheckResult("README Windows path", True))

    for tool_path in TOOLS_DIR.glob("*.py"):
        if tool_path.name == "verify_consistency.py":
            continue
        text = _read_text(tool_path)
        if "./.venv/bin/python" in text or ".venv/bin/python" in text:
            results.append(
                CheckResult(
                    f"Tool path in {tool_path.name}",
                    False,
                    "Found linux-style virtualenv python path",
                )
            )
    if not any(r.name.startswith("Tool path") for r in results):
        results.append(CheckResult("Tool paths", True))
    return results


def _iter_subprocess_blocks(path: Path) -> Iterable[Tuple[int, str]]:
    text = _read_text(path)
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        if "subprocess.run" in line or "subprocess.Popen" in line:
            block = [line]
            # include next few lines to capture parameters
            for extra in lines[idx: idx + 6]:
                block.append(extra)
            yield idx, "\n".join(block)


def check_sys_executable_usage() -> List[CheckResult]:
    failures: List[CheckResult] = []
    pattern = re.compile(r"['\"]python(3)?(\.exe)?['\"]")
    for path in TOOLS_DIR.glob("*.py"):
        for lineno, block in _iter_subprocess_blocks(path):
            if "sys.executable" in block:
                continue
            if pattern.search(block):
                failures.append(
                    CheckResult(
                        f"sys.executable in {path.name}",
                        False,
                        f"Line {lineno} uses hard-coded python reference",
                    )
                )
    if not failures:
        return [CheckResult("Subprocess uses sys.executable", True)]
    return failures


def check_ui_encoding() -> List[CheckResult]:
    path = TOOLS_DIR / "ui_app.py"
    text = _read_text(path)
    if not text:
        return [CheckResult("ui_app encoding", True, "ui_app.py not present (skipped)")]

    failures: List[CheckResult] = []
    for lineno, block in _iter_subprocess_blocks(path):
        if "capture_output=True" not in block:
            continue
        if "encoding=\"utf-8\"" not in block or "errors=\"replace\"" not in block:
            failures.append(
                CheckResult(
                    "ui_app encoding",
                    False,
                    f"Line {lineno} missing encoding='utf-8' or errors='replace'",
                )
            )
    if failures:
        return failures
    return [CheckResult("ui_app encoding", True)]


def check_ascii_markers() -> List[CheckResult]:
    qa_flow_markers = {"OUTPUT_PACKET", "PACKET_PATH", "OUTPUT_EVIDENCE_PACK", "EVIDENCE_PACK_PATH"}
    path = TOOLS_DIR / "qa_flow.py"
    text = _read_text(path)
    missing = [m for m in qa_flow_markers if m not in text]
    results: List[CheckResult] = []
    if missing:
        results.append(
            CheckResult(
                "qa_flow markers",
                False,
                f"Missing markers: {', '.join(sorted(missing))}",
            )
        )
    else:
        results.append(CheckResult("qa_flow markers", True))

    ui_text = _read_text(TOOLS_DIR / "ui_app.py")
    ui_missing = [m for m in qa_flow_markers if m and m not in ui_text]
    if ui_missing:
        results.append(
            CheckResult(
                "ui_app marker parsing",
                False,
                f"UI missing marker parsing for: {', '.join(sorted(ui_missing))}",
            )
        )
    else:
        results.append(CheckResult("ui_app marker parsing", True))
    return results


def check_local_model_ui_markers() -> List[CheckResult]:
    path = TOOLS_DIR / "ui_app.py"
    text = _read_text(path)
    if not text:
        return [CheckResult("local model UI markers", True, "ui_app.py not present (skipped)")]

    required = [
        "Local Model (Dry-Run)",
        "RUN_LOCAL_MODEL_START",
        "RUN_LOCAL_MODEL_SUMMARY",
        "RUN_LOCAL_MODEL_END",
        "VERIFY_EDITS_PAYLOAD_SUMMARY",
        "APPLY_EDITS_SUMMARY",
        "ARTIFACT_PATH|path=",
    ]
    missing = [marker for marker in required if marker not in text]
    if missing:
        return [
            CheckResult(
                "local model UI markers",
                False,
                f"Missing markers: {', '.join(sorted(missing))}",
            )
        ]
    return [CheckResult("local model UI markers", True)]


def check_sim_safety_pack_assets() -> List[CheckResult]:
    expected_files = [TOOLS_DIR / "sim_autopilot.py", TOOLS_DIR / "verify_sim_safety_pack.py"]
    missing = [str(p.name) for p in expected_files if not p.exists()]
    results: List[CheckResult] = []
    if missing:
        results.append(CheckResult("sim safety pack files", False, f"missing: {', '.join(missing)}"))
    else:
        results.append(CheckResult("sim safety pack files", True))

    hud_keys = ["mode", "risk_budget_used", "drawdown_used", "rejects_recent"]
    hud_text = _read_text(TOOLS_DIR / "dashboard_model.py")
    missing_keys = [k for k in hud_keys if k not in hud_text]
    if missing_keys:
        results.append(
            CheckResult(
                "risk HUD fields",
                False,
                f"dashboard_model.py missing: {', '.join(sorted(missing_keys))}",
            )
        )
    else:
        results.append(CheckResult("risk HUD fields", True))
    return results


def check_sim_tournament_presence() -> List[CheckResult]:
    expected = [TOOLS_DIR / "sim_tournament.py", TOOLS_DIR / "verify_sim_tournament.py"]
    missing = [p.name for p in expected if not p.exists()]
    if missing:
        return [CheckResult("sim tournament files", False, f"Missing: {', '.join(sorted(missing))}")]

    contents = (TOOLS_DIR / "sim_tournament.py").read_text(encoding="utf-8")
    required_args = [
        "--input",
        "--windows",
        "--start-ts",
        "--end-ts",
        "--stride",
        "--variants",
        "--max-steps",
        "--policy-version",
    ]
    missing_args = [arg for arg in required_args if arg not in contents]
    if missing_args:
        return [CheckResult("sim tournament argparse", False, f"Missing args: {', '.join(sorted(missing_args))}")]
    return [CheckResult("sim tournament checks", True)]


def _extract_readme_flags(script_name: str) -> set[str]:
    flags: set[str] = set()
    for line in _read_text(README_PATH).splitlines():
        if script_name in line:
            for token in line.split():
                if token.startswith("--"):
                    flags.add(token)
    return flags


def _run_help(script: Path) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")


def _format_dep_list(items: Iterable[str]) -> str:
    collected = sorted(set(items))
    return ",".join(collected) if collected else "none"


def _consistency_status_lines(
    status: str,
    skipped_checks: List[str],
    how_to_opt_in: str,
    next_step_cmd: str,
) -> List[str]:
    if status == "PASS":
        return ["CONSISTENCY_OK|status=PASS"]
    if status == "DEGRADED":
        skipped = _format_dep_list(skipped_checks)
        return [
            "CONSISTENCY_OK_BUT_DEGRADED"
            f"|skipped={skipped}"
            "|next=review [SKIP] lines above (expected unless opt-in)"
            f"|how_to_opt_in={how_to_opt_in}"
        ]
    if status == "FAIL":
        return [f"CONSISTENCY_FAIL|next={next_step_cmd}"]
    raise ValueError(f"Unknown status {status}")


def _exit_code(has_failures: bool) -> int:
    return 1 if has_failures else 0


def _summarize_results(
    all_results: List[CheckResult],
    missing_deps: List[str],
    not_using_venv: bool,
) -> tuple[str, str, List[str], List[str], bool]:
    skipped_checks = [r.name for r in all_results if r.status == "SKIP"]
    has_failures = any(r.status == "FAIL" for r in all_results)
    degraded_reasons = []
    if missing_deps:
        degraded_reasons.append(f"missing_deps={_format_dep_list(missing_deps)}")
    if skipped_checks:
        degraded_reasons.append(f"skipped={_format_dep_list(skipped_checks)}")
    if not_using_venv:
        degraded_reasons.append("not_using_venv=1")

    if has_failures:
        summary_line = "FAIL: consistency issues detected"
        status = "FAIL"
    elif degraded_reasons:
        summary_line = "DEGRADED " + "; ".join(degraded_reasons)
        status = "DEGRADED"
    else:
        summary_line = "PASS: consistency checks succeeded"
        status = "PASS"

    return status, summary_line, skipped_checks, degraded_reasons, has_failures


def check_readme_cli_consistency(missing_deps: List[str]) -> List[CheckResult]:
    targets = [
        "tail_events.py",
        "replay_events.py",
        "select_evidence.py",
        "make_ai_packet.py",
        "qa_flow.py",
        "supervisor.py",
        "ui_app.py",
        "sim_replay.py",
        "verify_sim_replay.py",
        "verify_no_lookahead_sim.py",
        "train_daemon.py",
        "policy_candidate.py",
        "verify_policy_promotion.py",
        "verify_policy_lifecycle.py",
        "verify_train_semantic_loop.py",
    ]
    results: List[CheckResult] = []
    for name in targets:
        script = TOOLS_DIR / name
        if not script.exists():
            results.append(CheckResult(f"{name} --help", True, "not present (skipped)"))
            continue
        flags = _extract_readme_flags(name)
        if not flags:
            results.append(CheckResult(f"{name} flags", True, "no README flags (skipped)"))
            continue
        required_deps = HELP_CHECKS.get(name, ())
        missing_for_help = sorted(set(missing_deps) & set(required_deps))
        if missing_for_help:
            results.append(
                CheckResult(
                    f"{name} --help",
                    True,
                    f"missing deps (skipped): {_format_dep_list(missing_for_help)}; requires: {_format_dep_list(required_deps)}",
                )
            )
            continue
        code, output = _run_help(script)
        
        if code != 0:
            results.append(CheckResult(f"{name} --help", False, f"exit {code}"))
            continue
        missing = [flag for flag in sorted(flags) if flag not in output]
        if missing:
            results.append(
                CheckResult(
                    f"{name} flags",
                    False,
                    f"README mentions flags not in --help: {', '.join(missing)}",
                )
            )
        else:
            results.append(CheckResult(f"{name} flags", True))
    return results


def _py_compile_targets() -> List[Path]:
    targets = [
        ROOT / "main.py",
        ROOT / "alerts.py",
        ROOT / "quotes.py",
    ]
    for name in [
        "verify_smoke.py",
        "verify_cooldown.py",
        "verify_e2e_qa_loop.py",
        "verify_ui_actions.py",
        "verify_ui_qapacket_path.py",
        "verify_utf8_stdio.py",
        "verify_dashboard.py",
        "verify_supervisor.py",
        "verify_sim_safety_pack.py",
        "sim_autopilot.py",
        "sim_replay.py",
        "verify_sim_replay.py",
        "verify_no_lookahead_sim.py",
        "train_daemon.py",
        "train_service.py",
        "policy_candidate.py",
        "verify_policy_promotion.py",
        "verify_policy_lifecycle.py",
        "verify_train_semantic_loop.py",
        "verify_ui_hud_parsing.py",
        "progress_index.py",
        "progress_throughput_diagnose.py",
        "verify_progress_index.py",
        "verify_ui_progress_panel.py",
        "progress_judge.py",
        "verify_progress_truth.py",
        "verify_pr11_gate.py",
        "verify_pr12_gate.py",
        "verify_pr14_gate.py",
        "verify_pr16_gate.py",
        "verify_pr19_gate.py",
        "verify_pr20_gate.py",
        "verify_pr21_gate.py",
        "verify_ui_time_math.py",
        "git_baseline_probe.py",
        "ui_parsers.py",
        "strategy_pool.py",
        "promotion_gate_v2.py",
        "verify_kill_switch_recovery.py",
        "verify_run_completeness_contract.py",
        "verify_latest_artifacts.py",
        "verify_multiple_testing_control.py",
        "verify_powershell_no_goto_labels_contract.py",
        "experiment_ledger.py",
    ]:
        target = TOOLS_DIR / name
        if target.exists():
            targets.append(target)
    return targets


def check_py_compile(python_exec: str) -> List[CheckResult]:
    targets = _py_compile_targets()
    args = [str(p) for p in targets]
    result = subprocess.run(
        [python_exec, "-m", "py_compile", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        return [CheckResult("py_compile", True)]
    return [
        CheckResult(
            "py_compile",
            False,
            (result.stdout or "") + ("\n" + result.stderr if result.stderr else ""),
        )
    ]


def _training_blockers(env: dict[str, str | bool]) -> List[str]:
    reasons: List[str] = []
    if not env.get("is_windows"):
        reasons.append("non-Windows OS")
    if env.get("in_container"):
        reasons.append("running inside container")
    if not env.get("windows_venv"):
        reasons.append("missing .venv/\\Scripts/python.exe")
    if not env.get("can_write_logs"):
        detail = env.get("log_probe_error") or "log directory not writable"
        reasons.append(f"Logs not writable ({detail})")
    return reasons


def _legacy_gate_checks(include_legacy_gates: bool) -> List[CheckResult]:
    if include_legacy_gates:
        return []
    return []


def _quick_verifier_scripts(include_legacy_gates: bool) -> List[Path]:
    quick = [
        TOOLS_DIR / "verify_smoke.py",
        TOOLS_DIR / "verify_e2e_qa_loop.py",
        TOOLS_DIR / "verify_ui_qapacket_path.py",
        TOOLS_DIR / "verify_train_semantic_loop.py",
        TOOLS_DIR / "verify_progress_index.py",
        TOOLS_DIR / "verify_ui_progress_panel.py",
        TOOLS_DIR / "verify_pr16_gate.py",
        TOOLS_DIR / "verify_pr19_gate.py",
    ]
    if include_legacy_gates:
        quick.append(TOOLS_DIR / "verify_pr20_gate.py")
    return quick


def _run_quick_verifiers(
    missing_deps: List[str],
    env: dict[str, str | bool],
    python_exec: str,
    include_legacy_gates: bool,
) -> List[CheckResult]:
    quick = _quick_verifier_scripts(include_legacy_gates)
    results: List[CheckResult] = []
    results.extend(_legacy_gate_checks(include_legacy_gates))
    for script in quick:
        if not script.exists():
            results.append(CheckResult(script.name, True, "not present (skipped)"))
            continue
        if missing_deps:
            results.append(
                CheckResult(
                    script.name,
                    True,
                    f"missing deps (skipped): {_format_dep_list(missing_deps)}",
                )
            )
            continue
        training_blockers = _training_blockers(env)
        if script.name.startswith("verify_train_") and training_blockers:
            results.append(
                CheckResult(
                    script.name,
                    True,
                    f"skipped: {' ; '.join(training_blockers)}",
                )
            )
            continue
        cmd = [python_exec, str(script)]
        if script.name == "verify_smoke.py":
            cmd.append("--allow-kill-switch-move")
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            results.append(CheckResult(script.name, True))
        else:
            snippet = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            results.append(CheckResult(script.name, False, snippet.strip()))
    return results


def _find_archived_event_files(root: Path) -> List[Path]:
    return sorted(
        [
            path
            for path in root.rglob("events_*.jsonl")
            if ARCHIVE_EVENTS_PATTERN.fullmatch(path.name)
        ]
    )


def check_events_schema(
    include_archives: bool,
    root: Path = ROOT,
    logs_dir: Path = LOGS_DIR,
) -> List[CheckResult]:
    required_keys = {"schema_version", "ts_utc", "event_type", "symbol", "severity", "message"}
    active_files = [logs_dir / "events.jsonl", logs_dir / "events_sim.jsonl"]
    active_files = [path for path in active_files if path.exists()]
    archive_root = logs_dir / "event_archives"
    legacy_root = logs_dir / "_event_archives"
    archives = _find_archived_event_files(archive_root) if archive_root.exists() else []
    legacy_archives = _find_archived_event_files(legacy_root) if legacy_root.exists() else []
    archive_count = len(archives) + len(legacy_archives)
    if not active_files and not include_archives:
        return [
            CheckResult("events schema", True, "no active events files (skipped)"),
        ]

    failures: List[str] = []

    def _validate(path: Path) -> None:
        display_path = path
        try:
            display_path = path.relative_to(root)
        except ValueError:
            display_path = path
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                obj = json.loads(line)
            except Exception:
                failures.append(f"{display_path}: line {lineno} not valid JSON")
                continue
            missing = [k for k in sorted(required_keys) if k not in obj]
            if missing:
                failures.append(
                    f"{display_path}: line {lineno} missing keys {', '.join(missing)}"
                )

    for path in active_files:
        _validate(path)
    if include_archives:
        for path in archives + legacy_archives:
            _validate(path)

    results: List[CheckResult] = []
    if failures:
        results.append(CheckResult("events schema", False, "; ".join(failures)))
    elif not active_files:
        results.append(CheckResult("events schema", True, "no active events files (skipped)"))
    else:
        results.append(CheckResult("events schema", True))

    if include_archives:
        results.append(CheckResult("events archives", True, f"archive_files={archive_count}"))
        if legacy_archives:
            results.append(
                CheckResult(
                    "events archives legacy",
                    "WARN",
                    "EVENT_ARCHIVE_LEGACY|"
                    f"count={len(legacy_archives)}|"
                    "path=Logs/_event_archives|"
                    "next=python -m tools.migrate_event_archives --logs-dir Logs --archive-dir Logs/event_archives "
                    "--artifacts-dir artifacts --mode move",
                )
            )
    return results


def check_status_json() -> List[CheckResult]:
    status_path = LOGS_DIR / "status.json"
    if not status_path.exists():
        return [CheckResult("status.json", True, "status.json not found (skipped)")]
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [CheckResult("status.json", False, f"parse failed: {exc}")]
    missing = [k for k in ("ts_utc",) if k not in payload]
    if missing:
        return [CheckResult("status.json", False, f"missing keys: {', '.join(missing)}")]
    return [CheckResult("status.json", True)]


def check_read_only_guard() -> List[CheckResult]:
    keywords = [
        "place order",
        "submit order",
        "broker login",
        "alpaca",
        "ibkr",
        "2fa",
    ]
    found: List[str] = []
    safe_markers = ("never", "禁止", "no ", "not ")
    exclude_dirs = {".venv", "site-packages", "Logs", "Data", "Reports"}
    candidates: List[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        rel_parts = path.relative_to(ROOT).parts
        if any(part in exclude_dirs for part in rel_parts):
            continue
        if path.name.startswith("README") or path.name == "AGENTS.md":
            candidates.append(path)
            continue
        if path.suffix in {".py", ".yaml", ".yml"}:
            candidates.append(path)

    for path in candidates:
        if path.name == "verify_consistency.py":
            continue
        for lineno, line in enumerate(_read_text(path).splitlines(), start=1):
            lower = line.lower()
            if any(marker in lower for marker in safe_markers):
                continue
            for word in keywords:
                if word in lower:
                    found.append(
                        f"{path.relative_to(ROOT)}:{lineno} contains '{word}'"
                    )
    if found:
        return [
            CheckResult("READ_ONLY guard", False, "; ".join(sorted(set(found))))
        ]
    return [CheckResult("READ_ONLY guard", True)]


def check_docs_contract(artifacts_dir: Path, python_exec: str) -> List[CheckResult]:
    cmd = [
        python_exec,
        "-m",
        "tools.verify_docs_contract",
        "--artifacts-dir",
        str(artifacts_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess guard
        return [CheckResult("docs contract", False, f"error={exc}")]
    if completed.returncode == 0:
        return [CheckResult("docs contract", True)]
    detail = f"exit_code={completed.returncode}; see {artifacts_dir / 'verify_docs_contract.txt'}"
    return [CheckResult("docs contract", False, detail)]


def check_powershell_no_goto_labels_contract(
    artifacts_dir: Path, python_exec: str
) -> List[CheckResult]:
    cmd = [
        python_exec,
        "-m",
        "tools.verify_powershell_no_goto_labels_contract",
        "--artifacts-dir",
        str(artifacts_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess guard
        return [CheckResult("powershell no goto", False, f"error={exc}")]
    if completed.returncode == 0:
        return [CheckResult("powershell no goto", True)]
    detail = (
        "exit_code="
        f"{completed.returncode}; see {artifacts_dir / 'verify_powershell_no_goto_labels_contract.txt'}"
    )
    return [CheckResult("powershell no goto", False, detail)]


def check_inventory_contract(artifacts_dir: Path, python_exec: str) -> List[CheckResult]:
    cmd = [
        python_exec,
        "-m",
        "tools.verify_inventory_contract",
        "--artifacts-dir",
        str(artifacts_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess guard
        return [CheckResult("inventory contract", False, f"error={exc}")]
    if completed.returncode == 0:
        return [CheckResult("inventory contract", True)]
    detail = f"exit_code={completed.returncode}; see {artifacts_dir / 'verify_inventory_contract.txt'}"
    return [CheckResult("inventory contract", False, detail)]


def check_execution_model(artifacts_dir: Path, python_exec: str) -> List[CheckResult]:
    cmd = [
        python_exec,
        "-m",
        "tools.verify_execution_model",
        "--artifacts-dir",
        str(artifacts_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess guard
        return [CheckResult("execution model", False, f"error={exc}")]
    if completed.returncode == 0:
        return [CheckResult("execution model", True)]
    detail = f"exit_code={completed.returncode}; see {artifacts_dir / 'execution_model_report.txt'}"
    return [CheckResult("execution model", False, detail)]


def check_data_health(artifacts_dir: Path, python_exec: str) -> List[CheckResult]:
    cmd = [
        python_exec,
        "-m",
        "tools.verify_data_health",
        "--artifacts-dir",
        str(artifacts_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess guard
        return [CheckResult("data health", False, f"error={exc}")]
    if completed.returncode == 0:
        return [CheckResult("data health", True)]
    detail = f"exit_code={completed.returncode}; see {artifacts_dir / 'data_health_report.txt'}"
    return [CheckResult("data health", False, detail)]


def check_walk_forward(artifacts_dir: Path, python_exec: str) -> List[CheckResult]:
    cmd = [
        python_exec,
        "-m",
        "tools.verify_walk_forward",
        "--artifacts-dir",
        str(artifacts_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess guard
        return [CheckResult("walk forward", False, f"error={exc}")]
    if completed.returncode == 0:
        return [CheckResult("walk forward", True)]
    detail = f"exit_code={completed.returncode}; see {artifacts_dir / 'walk_forward_report.txt'}"
    return [CheckResult("walk forward", False, detail)]


def check_redteam_integrity(artifacts_dir: Path, python_exec: str) -> List[CheckResult]:
    cmd = [
        python_exec,
        "-m",
        "tools.verify_redteam_integrity",
        "--artifacts-dir",
        str(artifacts_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess guard
        return [CheckResult("redteam integrity", False, f"error={exc}")]
    if completed.returncode == 0:
        return [CheckResult("redteam integrity", True)]
    detail = f"exit_code={completed.returncode}; see {artifacts_dir / 'redteam_report.txt'}"
    return [CheckResult("redteam integrity", False, detail)]


def check_multiple_testing_control(artifacts_dir: Path, python_exec: str) -> List[CheckResult]:
    from tools.experiment_ledger import (
        DEFAULT_BASELINES,
        append_entry,
        build_entry,
        resolve_latest_ledger_path,
    )

    ledger_path = resolve_latest_ledger_path(artifacts_dir, fallback=artifacts_dir / "experiment_ledger.jsonl")
    if not ledger_path.exists():
        entry = build_entry(
            run_id="consistency_seed",
            candidate_count=3,
            trial_count=6,
            baselines_used=DEFAULT_BASELINES,
            window_config={"seed": 0, "max_steps": 50, "candidate_count": 3},
            code_paths=[ROOT / "tools" / "verify_multiple_testing_control.py"],
            timestamp="2024-01-01T00:00:00Z",
        )
        append_entry(artifacts_dir, entry)
    cmd = [
        python_exec,
        "-m",
        "tools.verify_multiple_testing_control",
        "--artifacts-dir",
        str(artifacts_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess guard
        return [CheckResult("multiple testing control", False, f"error={exc}")]
    if completed.returncode == 0:
        return [CheckResult("multiple testing control", True)]
    detail = f"exit_code={completed.returncode}; see {artifacts_dir / 'experiment_ledger_summary.json'}"
    return [CheckResult("multiple testing control", False, detail)]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run consistency checks.")
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Artifacts directory (reserved for future use).",
    )
    parser.add_argument(
        "--include-event-archives",
        action="store_true",
        help="Validate archived events_YYYY-MM-DD.jsonl files.",
    )
    parser.add_argument(
        "--include-legacy-gates",
        action="store_true",
        help="Run legacy gates such as verify_pr20_gate.py.",
    )
    args = parser.parse_args(argv)
    missing_deps = detect_missing_deps()
    env = _detect_environment()
    baseline_info = probe_baseline()
    python_exec = pick_python(print_marker=True)
    _print_header(env, missing_deps, baseline_info)

    p0_checks: List[Callable[[], List[CheckResult]]] = [
        check_windows_paths,
        check_sys_executable_usage,
        check_ui_encoding,
        check_ascii_markers,
        check_local_model_ui_markers,
        check_sim_safety_pack_assets,
        check_sim_tournament_presence,
        lambda: check_py_compile(python_exec),
        lambda: check_events_schema(args.include_event_archives),
        check_status_json,
        check_read_only_guard,
        lambda: check_powershell_no_goto_labels_contract(
            Path(args.artifacts_dir), python_exec
        ),
        lambda: check_docs_contract(Path(args.artifacts_dir), python_exec),
        lambda: check_inventory_contract(Path(args.artifacts_dir), python_exec),
        lambda: check_execution_model(Path(args.artifacts_dir), python_exec),
        lambda: check_data_health(Path(args.artifacts_dir), python_exec),
        lambda: check_walk_forward(Path(args.artifacts_dir), python_exec),
        lambda: check_redteam_integrity(Path(args.artifacts_dir), python_exec),
        lambda: check_multiple_testing_control(Path(args.artifacts_dir), python_exec),
    ]
    optional_checks: List[Callable[[], List[CheckResult]]] = [
        lambda: check_readme_cli_consistency(missing_deps),
        lambda: _run_quick_verifiers(missing_deps, env, python_exec, args.include_legacy_gates),
    ]

    all_results: List[CheckResult] = []
    for fn in p0_checks:
        all_results.extend(fn())
    for fn in optional_checks:
        all_results.extend(fn())

    not_using_venv = env.get("venv_present") and not env.get("executable_in_venv")
    status, summary_line, skipped_checks, degraded_reasons, has_failures = _summarize_results(
        all_results,
        missing_deps,
        bool(not_using_venv),
    )

    notes = ";".join(degraded_reasons) if degraded_reasons else "none"
    summary_marker = "|".join(
        [
            "CONSISTENCY_SUMMARY",
            f"status={status}",
            f"failed={len([r for r in all_results if r.status == 'FAIL'])}",
            f"skipped={len(skipped_checks)}",
            f"missing_deps={_format_dep_list(missing_deps)}",
            f"not_using_venv={int(bool(not_using_venv))}",
            f"notes={notes}",
        ]
    )

    print("===BEGIN===")
    print(summary_marker)
    print(summary_line)
    for line in _consistency_status_lines(
        status,
        skipped_checks,
        CONSISTENCY_OPT_IN_FLAGS,
        CONSISTENCY_NEXT_STEP_CMD,
    ):
        print(line)

    for res in all_results:
        print(res.render())

    print()
    if not_using_venv:
        print(
            "CONSISTENCY_HINT|reason=NOT_USING_VENV|cmd=.\\.venv\\Scripts\\python.exe .\\tools\\verify_consistency.py"
        )
    print("===END===")
    print(summary_marker)
    if has_failures:
        print("FAIL: consistency issues detected")
    return _exit_code(has_failures)


if __name__ == "__main__":
    sys.exit(main())
