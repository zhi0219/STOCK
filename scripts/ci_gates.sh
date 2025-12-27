#!/usr/bin/env bash
set -euo pipefail

artifacts_dir="artifacts"
log_file="${artifacts_dir}/gates.log"

status="PASS"
rc=0
failing_gate=""
runner=""

mkdir -p "${artifacts_dir}"
: > "${log_file}"

exec > >(tee -a "${log_file}") 2>&1

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

  python3 - <<'PY'
from __future__ import annotations
from pathlib import Path
import json
import os
import platform
import subprocess
from datetime import datetime, timezone

artifacts_dir = Path("artifacts")
log_file = artifacts_dir / "gates.log"
summary_path = artifacts_dir / "proof_summary.json"

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

files = []
if artifacts_dir.exists():
    for path in sorted(artifacts_dir.rglob("*")):
        if path.is_file():
            files.append(str(path))

summary_path_str = str(summary_path)
if summary_path_str not in files:
    files.append(summary_path_str)

summary = {
    "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "git_commit": git_commit,
    "overall_status": status,
    "runner": os.environ.get("CI_GATES_RUNNER", ""),
    "failing_gate": failing_gate,
    "error_excerpt": error_excerpt,
    "environment": {
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "ci": True,
    },
    "files": files,
}

summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
PY
}

trap 'write_summary $?' EXIT

echo "===CI_GATES_START==="

if ls tools/verify_pr*_gate.py >/dev/null 2>&1; then
  mapfile -t pr_gates < <(ls tools/verify_pr*_gate.py 2>/dev/null | sort -V)
  last_index=$(( ${#pr_gates[@]} - 1 ))
  runner="python3 ${pr_gates[$last_index]}"
elif [[ -f tools/verify_foundation.py ]]; then
  runner="python3 tools/verify_foundation.py"
elif [[ -f tools/verify_consistency.py ]]; then
  runner="python3 tools/verify_consistency.py"
fi

if [[ -z "${runner}" ]]; then
  status="FAIL"
  failing_gate="runner_detection"
  rc=1
  echo "No canonical gate runner found."
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
