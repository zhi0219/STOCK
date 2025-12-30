from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _require_contains(text: str, needle: str, label: str, errors: list[str]) -> None:
    if needle not in text:
        errors.append(f"missing_{label}:{needle}")


def main() -> int:
    errors: list[str] = []

    run_ui_path = ROOT / "scripts" / "run_ui_windows.ps1"
    if not run_ui_path.exists():
        errors.append("run_ui_windows_missing")
    else:
        content = _read(run_ui_path)
        _require_contains(content, "UI_LAUNCH_START", "ui_launch_start", errors)
        _require_contains(content, "UI_PREFLIGHT_OK", "ui_preflight_ok", errors)
        _require_contains(content, "UI_LAUNCH_END", "ui_launch_end", errors)
        _require_contains(content, "-m tools.ui_app", "module_launch", errors)
        _require_contains(content, "if ((Test-Path", "test_path_parentheses", errors)

    gitattributes_path = ROOT / ".gitattributes"
    if gitattributes_path.exists():
        gitattributes = _read(gitattributes_path)
        _require_contains(gitattributes, "*.ps1 text eol=crlf", "ps1_eol", errors)
        _require_contains(gitattributes, "*.py text eol=lf", "py_eol", errors)
    else:
        errors.append("gitattributes_missing")

    ci_gates_path = ROOT / "scripts" / "ci_gates.sh"
    if ci_gates_path.exists():
        ci_gates = _read(ci_gates_path)
        _require_contains(ci_gates, "tools.ps_parse_guard", "ps_parse_guard", errors)
        _require_contains(ci_gates, "tools.ui_preflight", "ui_preflight_gate", errors)
        _require_contains(ci_gates, "--targets tools scripts", "compile_targets", errors)
    else:
        errors.append("ci_gates_missing")

    if errors:
        print("verify_pr40_gate FAIL")
        for err in errors:
            print(f" - {err}")
        return 1

    print("verify_pr40_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
