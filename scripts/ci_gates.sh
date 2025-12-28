#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

artifacts_dir="artifacts"
log_file="${artifacts_dir}/gates.log"

status="PASS"
rc=0
failing_gate=""
runner=""
runner_module=""
runner_module_exists="0"

mkdir -p "${artifacts_dir}"
: > "${log_file}"

exec > >(tee -a "${log_file}") 2>&1

export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

python3 - <<'PY'
from __future__ import annotations
import os
import sys

root = os.environ.get("PYTHONPATH", "").split(":")[0]
print(f"CI_ROOT|root={root}|python={sys.version.split()[0]}|sys_path0={sys.path[0]}")
PY

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
  export CI_GATES_RUNNER_MODULE="${runner_module}"
  export CI_GATES_RUNNER_MODULE_EXISTS="${runner_module_exists}"

  python3 tools/action_center_report.py --output "${artifacts_dir}/action_center_report.json" || true

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
    "runner_module": os.environ.get("CI_GATES_RUNNER_MODULE", ""),
    "runner_module_exists": os.environ.get("CI_GATES_RUNNER_MODULE_EXISTS", "0"),
    "error_excerpt": error_excerpt,
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
    f"- **runner_module**: `{os.environ.get('CI_GATES_RUNNER_MODULE', '')}`",
    f"- **runner_module_exists**: `{os.environ.get('CI_GATES_RUNNER_MODULE_EXISTS', '0')}`",
    f"- **git_commit**: `{git_commit}`",
    f"- **ts_utc**: `{summary['ts_utc']}`",
    f"- **failing_gate**: `{failing_gate}`" if status == "FAIL" else "- **failing_gate**: `n/a`",
    f"- **error_excerpt**:\n\n```\n{error_excerpt}\n```" if status == "FAIL" and error_excerpt else "- **error_excerpt**: `n/a`",
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

import_contract_runner="python3 -m tools.verify_import_contract"
set +e
${import_contract_runner}
import_contract_exit=$?
set -e

if [[ ${import_contract_exit} -ne 0 ]]; then
  status="FAIL"
  failing_gate="verify_import_contract"
  rc=${import_contract_exit}
fi

if [[ ${rc} -eq 0 ]]; then
  if [[ -f tools/verify_foundation.py ]]; then
    runner_module="tools.verify_foundation"
    runner="python3 -m ${runner_module}"
  elif ls tools/verify_pr*_gate.py >/dev/null 2>&1; then
    mapfile -t pr_gates < <(ls tools/verify_pr*_gate.py 2>/dev/null | sort -V)
    last_index=$(( ${#pr_gates[@]} - 1 ))
    pr_gate_file="${pr_gates[$last_index]}"
    pr_gate_name="$(basename "${pr_gate_file}" .py)"
    runner_module="tools.${pr_gate_name}"
    runner="python3 -m ${runner_module}"
  elif [[ -f tools/verify_consistency.py ]]; then
    runner_module="tools.verify_consistency"
    runner="python3 -m ${runner_module}"
  fi
fi

if [[ -n "${runner_module}" ]]; then
  python3 - <<PY
from __future__ import annotations
import importlib.util
module_name = "${runner_module}"
spec = importlib.util.find_spec(module_name)
print(f"CI_GATE_RUNNER_MODULE_CHECK|module={module_name}|exists={int(spec is not None)}")
PY
  if python3 - <<PY
from __future__ import annotations
import importlib.util
module_name = "${runner_module}"
spec = importlib.util.find_spec(module_name)
raise SystemExit(0 if spec is not None else 1)
PY
  then
    runner_module_exists="1"
  else
    runner_module_exists="0"
  fi
fi

if [[ ${rc} -eq 0 ]]; then
  if [[ -z "${runner}" ]]; then
    status="FAIL"
    failing_gate="runner_detection"
    rc=1
    echo "No canonical gate runner found."
  elif [[ "${runner_module_exists}" == "0" ]]; then
    status="FAIL"
    failing_gate="runner_module_missing"
    rc=1
    echo "Canonical gate runner module missing: ${runner_module}"
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

if [[ "${CI_FORCE_FAIL:-0}" == "1" ]]; then
  echo "CI_FORCE_FAIL enabled; forcing failure after gates."
  status="FAIL"
  failing_gate="CI_FORCE_FAIL"
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
