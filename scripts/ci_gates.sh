#!/usr/bin/env bash
set -euo pipefail

artifacts_dir="artifacts"
log_file="${artifacts_dir}/gates.log"

status="PASS"
rc=0
failing_gate=""
runner=""
gate_script=""
import_contract_module=""

mkdir -p "${artifacts_dir}"
: > "${log_file}"

exec > >(tee -a "${log_file}") 2>&1

export PYTHONPATH="${PWD}"

write_summary() {
  local exit_code=${1}
  local summary_status="${status}"
  local summary_failing_gate="${failing_gate}"

  if [[ ${exit_code} -ne 0 ]]; then
    summary_status="FAIL"
    if [[ -z "${summary_failing_gate}" ]]; then
      summary_failing_gate="script_error"
    fi
  fi

  export CI_GATES_STATUS="${summary_status}"
  export CI_GATES_FAILING_GATE="${summary_failing_gate}"
  export CI_GATES_RUNNER="${runner}"

  python3 -m tools.action_center_report --output "${artifacts_dir}/action_center_report.json" || true

  python3 - <<'PY'
from __future__ import annotations
from pathlib import Path
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone

artifacts_dir = Path("artifacts")
log_file = artifacts_dir / "gates.log"
summary_path = artifacts_dir / "proof_summary.json"
job_summary_path = artifacts_dir / "ci_job_summary.md"

def sanitize_excerpt(text: str) -> str:
    if not text:
        return text
    redacted = text
    redacted = re.sub(
        r'(?i)\b(token|secret|password|api[_-]?key)\b\s*[:=]\s*([^\s,"\']+)',
        lambda m: f"{m.group(1)}=<REDACTED>",
        redacted,
    )
    redacted = re.sub(
        r'(?i)("?(token|secret|password|api[_-]?key)"?\s*[:=]\s*")([^"]+)(")',
        lambda m: f'{m.group(1)}<REDACTED>{m.group(4)}',
        redacted,
    )
    return redacted

def resolve_max_log_bytes() -> int:
    raw_bytes = os.environ.get("CI_MAX_LOG_BYTES")
    raw_kb = os.environ.get("CI_MAX_LOG_KB")
    if raw_bytes:
        try:
            return int(raw_bytes)
        except ValueError:
            return 2 * 1024 * 1024
    if raw_kb:
        try:
            return int(raw_kb) * 1024
        except ValueError:
            return 2048 * 1024
    return 2048 * 1024

max_log_bytes = resolve_max_log_bytes()
log_bytes_original = 0
log_bytes_final = 0
log_truncated = False

if log_file.exists():
    log_bytes_original = log_file.stat().st_size
    if log_bytes_original > max_log_bytes:
        marker = b"\n===LOG_TRUNCATED===\n"
        head_len = max_log_bytes // 2
        tail_len = max_log_bytes - head_len - len(marker)
        if tail_len < 0:
            head_len = max(0, max_log_bytes - len(marker))
            tail_len = 0
        with log_file.open("rb") as handle:
            head = handle.read(head_len)
            if tail_len > 0 and log_bytes_original > tail_len:
                handle.seek(-tail_len, os.SEEK_END)
                tail = handle.read(tail_len)
            else:
                tail = b""
        log_file.write_bytes(head + marker + tail)
        log_truncated = True
        log_bytes_final = log_file.stat().st_size
    else:
        log_bytes_final = log_bytes_original

try:
    git_commit = (
        subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True)
        .strip()
    )
except Exception:
    git_commit = "unknown"

status = os.environ.get("CI_GATES_STATUS", "PASS")
failing_gate = os.environ.get("CI_GATES_FAILING_GATE", "")

error_excerpt = ""
if log_file.exists() and status == "FAIL":
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    error_excerpt = "\n".join(lines[-80:])
    error_excerpt = sanitize_excerpt(error_excerpt)

import_contract_result_path = artifacts_dir / "import_contract_result.json"
import_contract_traceback_path = artifacts_dir / "import_contract_traceback.txt"
import_contract_result: dict[str, object] | None = None
import_contract_excerpt = ""
if import_contract_result_path.exists():
    try:
        import_contract_result = json.loads(
            import_contract_result_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        import_contract_result = {"status": "UNKNOWN", "module": None}
if import_contract_traceback_path.exists():
    trace_lines = import_contract_traceback_path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines()
    import_contract_excerpt = "\n".join(trace_lines[-60:])
    import_contract_excerpt = sanitize_excerpt(import_contract_excerpt)

syntax_guard_result_path = artifacts_dir / "syntax_guard_result.json"
syntax_guard_excerpt_path = artifacts_dir / "syntax_guard_excerpt.txt"
syntax_guard_status = "UNKNOWN"
syntax_guard_hits = 0
syntax_guard_excerpt = ""
if syntax_guard_result_path.exists():
    try:
        syntax_guard_result = json.loads(
            syntax_guard_result_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        syntax_guard_result = {"status": "UNKNOWN", "hits": 0}
    if isinstance(syntax_guard_result, dict):
        syntax_guard_status = str(syntax_guard_result.get("status", "UNKNOWN"))
        syntax_guard_hits = int(syntax_guard_result.get("hits", 0) or 0)
if syntax_guard_excerpt_path.exists():
    syntax_lines = syntax_guard_excerpt_path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines()
    syntax_guard_excerpt = "\n".join(syntax_lines[-60:])
    syntax_guard_excerpt = sanitize_excerpt(syntax_guard_excerpt)

ps_parse_result_path = artifacts_dir / "ps_parse_result.json"
ps_parse_status = "UNKNOWN"
ps_parse_reason = "unknown"
ps_parse_errors = 0
if ps_parse_result_path.exists():
    try:
        ps_parse_result = json.loads(
            ps_parse_result_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        ps_parse_result = {"status": "UNKNOWN", "reason": "unknown", "errors": []}
    if isinstance(ps_parse_result, dict):
        ps_parse_status = str(ps_parse_result.get("status", "UNKNOWN"))
        ps_parse_reason = str(ps_parse_result.get("reason", "unknown"))
        ps_parse_errors = len(ps_parse_result.get("errors") or [])

safe_push_contract_path = artifacts_dir / "safe_push_contract_result.json"
safe_push_contract_status = "UNKNOWN"
safe_push_contract_errors = []
if safe_push_contract_path.exists():
    try:
        safe_push_contract_result = json.loads(
            safe_push_contract_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        safe_push_contract_result = {"status": "UNKNOWN", "errors": []}
    if isinstance(safe_push_contract_result, dict):
        safe_push_contract_status = str(
            safe_push_contract_result.get("status", "UNKNOWN")
        )
        safe_push_contract_errors = safe_push_contract_result.get("errors") or []

repo_doctor_contract_path = artifacts_dir / "verify_repo_doctor_contract.json"
repo_doctor_contract_status = "UNKNOWN"
repo_doctor_contract_errors = []
if repo_doctor_contract_path.exists():
    try:
        repo_doctor_contract_result = json.loads(
            repo_doctor_contract_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        repo_doctor_contract_result = {"status": "UNKNOWN", "errors": []}
    if isinstance(repo_doctor_contract_result, dict):
        repo_doctor_contract_status = str(
            repo_doctor_contract_result.get("status", "UNKNOWN")
        )
        repo_doctor_contract_errors = repo_doctor_contract_result.get("errors") or []

powershell_join_path_path = artifacts_dir / "powershell_join_path_contract_result.json"
powershell_join_path_status = "UNKNOWN"
powershell_join_path_errors = []
if powershell_join_path_path.exists():
    try:
        powershell_join_path_result = json.loads(
            powershell_join_path_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        powershell_join_path_result = {"status": "UNKNOWN", "errors": []}
    if isinstance(powershell_join_path_result, dict):
        powershell_join_path_status = str(
            powershell_join_path_result.get("status", "UNKNOWN")
        )
        powershell_join_path_errors = powershell_join_path_result.get("errors") or []

ui_preflight_result_path = artifacts_dir / "ui_preflight_result.json"
ui_preflight_status = "UNKNOWN"
ui_preflight_reason = "unknown"
ui_preflight_repo_root = None
if ui_preflight_result_path.exists():
    try:
        ui_preflight_result = json.loads(
            ui_preflight_result_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        ui_preflight_result = {"status": "UNKNOWN", "reason": "unknown", "repo_root": None}
    if isinstance(ui_preflight_result, dict):
        ui_preflight_status = str(ui_preflight_result.get("status", "UNKNOWN"))
        ui_preflight_reason = str(ui_preflight_result.get("reason", "unknown"))
        ui_preflight_repo_root = ui_preflight_result.get("repo_root")

compile_result_path = artifacts_dir / "compile_check_result.json"
compile_log_path = artifacts_dir / "compile_check.log"
compile_result: dict[str, object] | None = None
compile_status = "UNKNOWN"
compile_exception = None
compile_error_location = None
compile_error_file = None
compile_error_line = None
compile_error_code = None
compile_excerpt = ""
if compile_result_path.exists():
    try:
        compile_result = json.loads(
            compile_result_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        compile_result = {"status": "UNKNOWN"}
if isinstance(compile_result, dict):
    compile_status = str(compile_result.get("status", "UNKNOWN"))
    compile_exception = compile_result.get("exception_summary") or compile_result.get(
        "exception"
    )
    compile_error_location = compile_result.get("error_location")
    if isinstance(compile_error_location, dict):
        compile_error_file = compile_error_location.get("file")
        compile_error_line = compile_error_location.get("line")
        compile_error_code = compile_error_location.get("code")
if compile_log_path.exists():
    compile_lines = compile_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    compile_excerpt = "\n".join(compile_lines[-60:])
    compile_excerpt = sanitize_excerpt(compile_excerpt)

files = []
if artifacts_dir.exists():
    for path in sorted(artifacts_dir.rglob("*")):
        if path.is_file():
            files.append(str(path))

summary_path_str = str(summary_path)
if summary_path_str not in files:
    files.append(summary_path_str)
job_summary_path_str = str(job_summary_path)
if job_summary_path_str not in files:
    files.append(job_summary_path_str)

summary = {
    "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "git_commit": git_commit,
    "overall_status": status,
    "runner": os.environ.get("CI_GATES_RUNNER", ""),
    "failing_gate": failing_gate,
    "error_excerpt": error_excerpt,
    "import_contract": {
        "result": import_contract_result,
        "traceback_excerpt": import_contract_excerpt,
    },
    "syntax_guard_status": syntax_guard_status,
    "syntax_guard_hits": syntax_guard_hits,
    "syntax_guard_excerpt_path": str(syntax_guard_excerpt_path),
    "ps_parse_status": ps_parse_status,
    "ps_parse_reason": ps_parse_reason,
    "ps_parse_errors": ps_parse_errors,
    "safe_push_contract_status": safe_push_contract_status,
    "safe_push_contract_errors": safe_push_contract_errors,
    "repo_doctor_contract_status": repo_doctor_contract_status,
    "repo_doctor_contract_errors": repo_doctor_contract_errors,
    "powershell_join_path_status": powershell_join_path_status,
    "powershell_join_path_errors": powershell_join_path_errors,
    "ui_preflight_status": ui_preflight_status,
    "ui_preflight_reason": ui_preflight_reason,
    "ui_preflight_repo_root": ui_preflight_repo_root,
    "compile_check_status": compile_status,
    "compile_check_exception": compile_exception,
    "compile_check_error_location": {
        "file": compile_error_file,
        "line": compile_error_line,
        "code": compile_error_code,
    },
    "compile_check_excerpt": compile_excerpt,
    "log_truncated": log_truncated,
    "log_bytes_original": log_bytes_original,
    "log_bytes_final": log_bytes_final,
    "max_log_bytes": max_log_bytes,
    "environment": {
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "ci": True,
    },
    "files": files,
}

artifacts_list = "\n".join(f"- `{item}`" for item in files)
summary_lines = [
    "# CI Gates Summary",
    "",
    f"- **overall_status**: `{status}`",
    f"- **runner**: `{os.environ.get('CI_GATES_RUNNER', '')}`",
    f"- **git_commit**: `{git_commit}`",
    f"- **ts_utc**: `{summary['ts_utc']}`",
    f"- **failing_gate**: `{failing_gate}`" if status == "FAIL" else "- **failing_gate**: `n/a`",
    f"- **error_excerpt**:\n\n```\n{error_excerpt}\n```" if status == "FAIL" and error_excerpt else "- **error_excerpt**: `n/a`",
    f"- **import_contract_status**: `{(import_contract_result or {}).get('status', 'UNKNOWN')}`",
    f"- **import_contract_module**: `{(import_contract_result or {}).get('module', 'n/a')}`",
    f"- **import_contract_exception**: `{(import_contract_result or {}).get('exception_type', 'none')}`",
    f"- **import_contract_traceback_excerpt**:\n\n```\n{import_contract_excerpt}\n```"
    if import_contract_excerpt
    else "- **import_contract_traceback_excerpt**: `n/a`",
    f"- **syntax_guard_status**: `{syntax_guard_status}`",
    f"- **syntax_guard_hits**: `{syntax_guard_hits}`",
    f"- **syntax_guard_excerpt_path**: `{syntax_guard_excerpt_path}`",
    f"- **syntax_guard_excerpt**:\n\n```\n{syntax_guard_excerpt}\n```"
    if syntax_guard_excerpt
    else "- **syntax_guard_excerpt**: `n/a`",
    f"- **ps_parse_status**: `{ps_parse_status}`",
    f"- **ps_parse_reason**: `{ps_parse_reason}`",
    f"- **ps_parse_errors**: `{ps_parse_errors}`",
    f"- **safe_push_contract_status**: `{safe_push_contract_status}`",
    f"- **safe_push_contract_errors**: `{safe_push_contract_errors}`",
    f"- **repo_doctor_contract_status**: `{repo_doctor_contract_status}`",
    f"- **repo_doctor_contract_errors**: `{repo_doctor_contract_errors}`",
    f"- **powershell_join_path_status**: `{powershell_join_path_status}`",
    f"- **powershell_join_path_errors**: `{powershell_join_path_errors}`",
    f"- **ui_preflight_status**: `{ui_preflight_status}`",
    f"- **ui_preflight_reason**: `{ui_preflight_reason}`",
    f"- **ui_preflight_repo_root**: `{ui_preflight_repo_root or 'n/a'}`",
    f"- **compile_check_status**: `{compile_status}`",
    f"- **compile_check_exception**: `{compile_exception or 'none'}`",
    f"- **compile_check_error_file**: `{compile_error_file or 'n/a'}`",
    f"- **compile_check_error_line**: `{compile_error_line or 'n/a'}`",
    f"- **compile_check_error_code**: `{compile_error_code or 'n/a'}`",
    f"- **compile_check_excerpt**:\n\n```\n{compile_excerpt}\n```"
    if compile_excerpt
    else "- **compile_check_excerpt**: `n/a`",
    f"- **log_truncated**: `{log_truncated}`",
    f"- **log_bytes_original**: `{log_bytes_original}`",
    f"- **log_bytes_final**: `{log_bytes_final}`",
    f"- **max_log_bytes**: `{max_log_bytes}`",
    "",
    "## Artifacts",
    artifacts_list or "- (none)",
]
job_summary_content = "\n".join(summary_lines).strip() + "\n"
job_summary_path.write_text(job_summary_content, encoding="utf-8")

step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
if step_summary_path:
    try:
        with Path(step_summary_path).open("a", encoding="utf-8") as handle:
            handle.write(job_summary_content)
    except Exception:
        pass

summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
PY
}

trap 'write_summary $?' EXIT

echo "===CI_GATES_START==="

if [[ "${CI_LOG_SPAM_DEMO:-0}" == "1" ]]; then
  echo "===CI_LOG_SPAM_DEMO_START==="
  for i in $(seq 1 500); do
    printf 'CI_LOG_SPAM_DEMO line %04d: harmless filler for truncation demo\n' "${i}"
  done
  echo "===CI_LOG_SPAM_DEMO_END==="
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.compile_check --targets tools scripts --artifacts-dir "${artifacts_dir}"
  compile_exit=$?
  set -e
  if [[ ${compile_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="compile_check"
    rc=${compile_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.syntax_guard --artifacts-dir "${artifacts_dir}"
  syntax_guard_exit=$?
  set -e
  if [[ ${syntax_guard_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="syntax_guard"
    rc=${syntax_guard_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.ps_parse_guard --script scripts/run_ui_windows.ps1 --artifacts-dir "${artifacts_dir}"
  ps_parse_exit=$?
  set -e
  if [[ ${ps_parse_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="ps_parse_guard"
    rc=${ps_parse_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.safe_push_contract --artifacts-dir "${artifacts_dir}"
  safe_push_contract_exit=$?
  set -e
  if [[ ${safe_push_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="safe_push_contract"
    rc=${safe_push_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_repo_doctor_contract --artifacts-dir "${artifacts_dir}"
  repo_doctor_contract_exit=$?
  set -e
  if [[ ${repo_doctor_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_repo_doctor_contract"
    rc=${repo_doctor_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_win_daily_green_contract --artifacts-dir "${artifacts_dir}"
  win_daily_green_contract_exit=$?
  set -e
  if [[ ${win_daily_green_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_win_daily_green_contract"
    rc=${win_daily_green_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_windows_foundation_workflow --artifacts-dir "${artifacts_dir}"
  workflow_contract_exit=$?
  set -e
  if [[ ${workflow_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_windows_foundation_workflow"
    rc=${workflow_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.autoheal_collect --artifacts-dir "${artifacts_dir}/autoheal"
  autoheal_collect_exit=$?
  set -e
  if [[ ${autoheal_collect_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="autoheal_collect"
    rc=${autoheal_collect_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_autoheal_contract --artifacts-dir "${artifacts_dir}/autoheal"
  autoheal_contract_exit=$?
  set -e
  if [[ ${autoheal_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_autoheal_contract"
    rc=${autoheal_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_safe_pull_contract \
    --artifacts-dir "${artifacts_dir}" \
    --input-dir "fixtures/safe_pull_contract/good"
  safe_pull_contract_exit=$?
  set -e
  if [[ ${safe_pull_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_safe_pull_contract"
    rc=${safe_pull_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_write_allowlist_contract --artifacts-dir "${artifacts_dir}"
  write_allowlist_contract_exit=$?
  set -e
  if [[ ${write_allowlist_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_write_allowlist_contract"
    rc=${write_allowlist_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_powershell_join_path_contract --artifacts-dir "${artifacts_dir}"
  powershell_join_path_exit=$?
  set -e
  if [[ ${powershell_join_path_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="powershell_join_path_contract"
    rc=${powershell_join_path_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_powershell_null_safe_trim_contract --artifacts-dir "${artifacts_dir}"
  powershell_trim_exit=$?
  set -e
  if [[ ${powershell_trim_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_powershell_null_safe_trim_contract"
    rc=${powershell_trim_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_powershell_no_goto_labels_contract --artifacts-dir "${artifacts_dir}"
  powershell_no_goto_exit=$?
  set -e
  if [[ ${powershell_no_goto_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_powershell_no_goto_labels_contract"
    rc=${powershell_no_goto_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.ui_preflight --ci --artifacts-dir "${artifacts_dir}"
  ui_preflight_exit=$?
  set -e
  if [[ ${ui_preflight_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="ui_preflight"
    rc=${ui_preflight_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_docs_contract --artifacts-dir "${artifacts_dir}"
  docs_contract_exit=$?
  set -e
  if [[ ${docs_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_docs_contract"
    rc=${docs_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_edits_contract --artifacts-dir "${artifacts_dir}"
  edits_contract_exit=$?
  set -e
  if [[ ${edits_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_edits_contract"
    rc=${edits_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.inventory_repo --artifacts-dir "${artifacts_dir}" --write-docs
  inventory_exit=$?
  set -e
  if [[ ${inventory_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="inventory_repo"
    rc=${inventory_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_inventory_contract --artifacts-dir "${artifacts_dir}"
  inventory_contract_exit=$?
  set -e
  if [[ ${inventory_contract_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_inventory_contract"
    rc=${inventory_contract_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_execution_model --artifacts-dir "${artifacts_dir}"
  execution_model_exit=$?
  set -e
  if [[ ${execution_model_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_execution_model"
    rc=${execution_model_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_data_health --artifacts-dir "${artifacts_dir}"
  data_health_exit=$?
  set -e
  if [[ ${data_health_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_data_health"
    rc=${data_health_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_walk_forward --artifacts-dir "${artifacts_dir}"
  walk_forward_exit=$?
  set -e
  if [[ ${walk_forward_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_walk_forward"
    rc=${walk_forward_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_redteam_integrity --artifacts-dir "${artifacts_dir}"
  redteam_exit=$?
  set -e
  if [[ ${redteam_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_redteam_integrity"
    rc=${redteam_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 - <<'PY'
from pathlib import Path

from tools.experiment_ledger import DEFAULT_BASELINES, append_entry, build_entry
from tools.paths import repo_root

ROOT = repo_root()
artifacts_dir = Path("artifacts").resolve()
entry = build_entry(
    run_id="ci_multitest_seed",
    candidate_count=3,
    trial_count=6,
    baselines_used=DEFAULT_BASELINES,
    window_config={"seed": 0, "max_steps": 50, "candidate_count": 3},
    code_paths=[ROOT / "tools" / "verify_multiple_testing_control.py"],
)
append_entry(artifacts_dir, entry)
PY
  ledger_seed_exit=$?
  set -e
  if [[ ${ledger_seed_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="experiment_ledger_seed"
    rc=${ledger_seed_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.verify_multiple_testing_control --artifacts-dir "${artifacts_dir}"
  multitest_exit=$?
  set -e
  if [[ ${multitest_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="verify_multiple_testing_control"
    rc=${multitest_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.apply_edits --repo . --edits fixtures/edits_contract/good.json --artifacts-dir "${artifacts_dir}" --dry-run
  edits_apply_exit=$?
  set -e
  if [[ ${edits_apply_exit} -ne 0 ]]; then
    status="FAIL"
    failing_gate="apply_edits_dry_run"
    rc=${edits_apply_exit}
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  set +e
  python3 -m tools.extract_json_strict \
    --raw-text fixtures/extract_json_strict/bad_fenced.txt \
    --out-json "${artifacts_dir}/extract_json_strict_bad.json"
  extract_exit=$?
  set -e
  if [[ ${extract_exit} -eq 0 ]]; then
    status="FAIL"
    failing_gate="extract_json_strict_negative"
    rc=1
  fi
fi

if ls tools/verify_pr*_gate.py >/dev/null 2>&1; then
  mapfile -t pr_gates < <(ls tools/verify_pr*_gate.py 2>/dev/null | sort -V)
  last_index=$(( ${#pr_gates[@]} - 1 ))
  gate_script="${pr_gates[$last_index]}"
  runner="python3 ${gate_script}"
elif [[ -f tools/verify_foundation.py ]]; then
  gate_script="tools/verify_foundation.py"
  runner="python3 ${gate_script}"
elif [[ -f tools/verify_consistency.py ]]; then
  gate_script="tools/verify_consistency.py"
  runner="python3 ${gate_script}"
fi

if [[ -z "${runner}" ]]; then
  status="FAIL"
  failing_gate="runner_detection"
  rc=1
  echo "No canonical gate runner found."
else
  preflight_gate="tools/verify_pr36_gate.py"

  if [[ -f "${preflight_gate}" && "${gate_script}" != "${preflight_gate}" ]]; then
    set +e
    python3 "${preflight_gate}"
    preflight_exit=$?
    set -e
    if [[ ${preflight_exit} -ne 0 ]]; then
      status="FAIL"
      failing_gate="python3 ${preflight_gate}"
      rc=${preflight_exit}
    fi
  fi

  if [[ ${rc} -eq 0 ]]; then
    import_contract_module="${gate_script%.py}"
    import_contract_module="${import_contract_module#./}"
    import_contract_module="${import_contract_module//\//.}"

    set +e
    python3 tools/verify_import_contract.py \
      --module "${import_contract_module}" \
      --artifacts-dir "${artifacts_dir}"
    import_contract_exit=$?
    set -e

    if [[ -f "import_contract_result.json" ]]; then
      cp "import_contract_result.json" "${artifacts_dir}/" || true
    fi
    if [[ -f "import_contract_traceback.txt" ]]; then
      cp "import_contract_traceback.txt" "${artifacts_dir}/" || true
    fi

    if [[ ${import_contract_exit} -ne 0 ]]; then
      status="FAIL"
      failing_gate="import_contract"
      rc=${import_contract_exit}
      echo "Import contract failed; skipping gate runner."
    else
      set +e
      ${runner}
      runner_exit=$?
      set -e
      if [[ ${runner_exit} -ne 0 ]]; then
        status="FAIL"
        failing_gate="${runner}"
        rc=${runner_exit}
      fi
    fi
  fi
fi

if [[ "${CI_FORCE_FAIL:-0}" == "1" ]]; then
  echo "CI_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="CI_FORCE_FAIL"
  rc=1
fi

if [[ "${PR30_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR30_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR30_FORCE_FAIL"
  rc=1
fi

if [[ "${PR31_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR31_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR31_FORCE_FAIL"
  rc=1
fi

if [[ "${PR32_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR32_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR32_FORCE_FAIL"
  rc=1
fi

if [[ "${PR33_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR33_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR33_FORCE_FAIL"
  rc=1
fi

if [[ "${PR34_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR34_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR34_FORCE_FAIL"
  rc=1
fi

if [[ "${PR35_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR35_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR35_FORCE_FAIL"
  rc=1
fi

if [[ "${PR36_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR36_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR36_FORCE_FAIL"
  rc=1
fi

if [[ "${PR37_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR37_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR37_FORCE_FAIL"
  rc=1
fi

if [[ "${PR38_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR38_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR38_FORCE_FAIL"
  rc=1
fi

if [[ "${PR39_FORCE_FAIL:-0}" == "1" ]]; then
  echo "PR39_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="PR39_FORCE_FAIL"
  rc=1
fi

echo "===CI_GATES_END==="

find . -path "./artifacts" -prune -o -type f -name "run_complete.json" -print0 | \
  while IFS= read -r -d '' file; do
    cp --parents "${file}" "${artifacts_dir}" || true
  done

find . -path "./artifacts" -prune -o -type f -name "*_latest.json" -print0 | \
  while IFS= read -r -d '' file; do
    cp --parents "${file}" "${artifacts_dir}" || true
  done

exit ${rc}
  
# --- foundation gate (P0) ---
python -m tools.verify_foundation --artifacts-dir artifacts
