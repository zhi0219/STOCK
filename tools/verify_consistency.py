from __future__ import annotations

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
TOOLS_DIR = ROOT / "tools"
README_PATH = ROOT / "README.md"
LOGS_DIR = ROOT / "Logs"
OPTIONAL_DEPS = ("pandas", "yaml", "yfinance")
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


def _print_header(env: dict[str, str | bool], missing_deps: list[str]) -> None:
    marker = "|".join(
        [
            "CONSISTENCY_HEADER",
            f"os={platform.system()}",
            f"in_container={int(env.get('in_container', False))}",
            f"venv_present={int(env.get('venv_present', False))}",
            f"using_venv={int(env.get('executable_in_venv', False))}",
            f"can_write_logs={int(env.get('can_write_logs', False))}",
            f"missing_deps={_format_dep_list(missing_deps)}",
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
                    "SKIP",
                    f"missing deps: {_format_dep_list(missing_for_help)}; requires: {_format_dep_list(required_deps)}",
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
        "policy_candidate.py",
        "verify_policy_promotion.py",
        "verify_policy_lifecycle.py",
        "verify_train_semantic_loop.py",
        "verify_ui_hud_parsing.py",
        "progress_index.py",
        "verify_progress_index.py",
        "verify_ui_progress_panel.py",
        "progress_judge.py",
        "verify_progress_truth.py",
        "verify_pr11_gate.py",
    ]:
        target = TOOLS_DIR / name
        if target.exists():
            targets.append(target)
    return targets


def check_py_compile() -> List[CheckResult]:
    targets = _py_compile_targets()
    args = [str(p) for p in targets]
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", *args],
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


def _run_quick_verifiers(missing_deps: List[str], env: dict[str, str | bool]) -> List[CheckResult]:
    quick = [
        TOOLS_DIR / "verify_smoke.py",
        TOOLS_DIR / "verify_e2e_qa_loop.py",
        TOOLS_DIR / "verify_ui_qapacket_path.py",
        TOOLS_DIR / "verify_train_semantic_loop.py",
        TOOLS_DIR / "verify_progress_index.py",
        TOOLS_DIR / "verify_ui_progress_panel.py",
    ]
    results: List[CheckResult] = []
    for script in quick:
        if not script.exists():
            results.append(CheckResult(script.name, True, "not present (skipped)"))
            continue
        if missing_deps:
            results.append(
                CheckResult(
                    script.name,
                    "SKIP",
                    f"missing deps: {_format_dep_list(missing_deps)}",
                )
            )
            continue
        training_blockers = _training_blockers(env)
        if script.name.startswith("verify_train_") and training_blockers:
            results.append(
                CheckResult(
                    script.name,
                    "SKIP",
                    " ; ".join(training_blockers),
                )
            )
            continue
        cmd = [sys.executable, str(script)]
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


def check_events_schema() -> List[CheckResult]:
    required_keys = {"schema_version", "ts_utc", "event_type", "symbol", "severity", "message"}
    events_files = sorted(LOGS_DIR.glob("events_*.jsonl"))
    if not events_files:
        return [CheckResult("events schema", True, "no events files (skipped)")]

    failures: List[str] = []
    for path in events_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                obj = json.loads(line)
            except Exception:
                failures.append(f"{path.name}: line {lineno} not valid JSON")
                continue
            missing = [k for k in sorted(required_keys) if k not in obj]
            if missing:
                failures.append(
                    f"{path.name}: line {lineno} missing keys {', '.join(missing)}"
                )
    if failures:
        return [CheckResult("events schema", False, "; ".join(failures))]
    return [CheckResult("events schema", True)]


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


def main() -> int:
    missing_deps = detect_missing_deps()
    env = _detect_environment()
    _print_header(env, missing_deps)

    p0_checks: List[Callable[[], List[CheckResult]]] = [
        check_windows_paths,
        check_sys_executable_usage,
        check_ui_encoding,
        check_ascii_markers,
        check_sim_safety_pack_assets,
        check_sim_tournament_presence,
        check_py_compile,
        check_events_schema,
        check_status_json,
        check_read_only_guard,
    ]
    optional_checks: List[Callable[[], List[CheckResult]]] = [
        lambda: check_readme_cli_consistency(missing_deps),
        lambda: _run_quick_verifiers(missing_deps, env),
    ]

    all_results: List[CheckResult] = []
    for fn in p0_checks:
        all_results.extend(fn())
    for fn in optional_checks:
        all_results.extend(fn())

    skipped_checks = [r.name for r in all_results if r.status == "SKIP"]
    has_failures = any(r.status == "FAIL" for r in all_results)
    not_using_venv = env.get("venv_present") and not env.get("executable_in_venv")
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
        print("Next step: .\\.venv\\Scripts\\python.exe tools/verify_consistency.py")
    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
