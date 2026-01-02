param(
  [Parameter(Mandatory=$true)][string]$RepoRoot,
  [string]$Model = "qwen2.5-coder:7b-instruct"
)

$ErrorActionPreference="Stop"

function Write-Utf8NoBomLF([string]$Path,[string]$Content){
  $dir=[IO.Path]::GetDirectoryName($Path)
  if ($dir) { [IO.Directory]::CreateDirectory($dir) | Out-Null }
  $c = $Content -replace "`r`n","`n"
  $enc = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($Path,$c,$enc)
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$Artifacts = Join-Path $RepoRoot "artifacts"
$PDir = Join-Path $Artifacts "prompts\verify"
$DDir = Join-Path $Artifacts "draft_verify"
$Edits = Join-Path $Artifacts "edits_verify.json"

New-Item -Force -ItemType Directory $Artifacts,$PDir,$DDir | Out-Null

# ensure python
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { $Py = "python" }

# ensure tools/run_ollama.py exists (minimal runner)
$runOllama = Join-Path $RepoRoot "tools\run_ollama.py"
if (-not (Test-Path -LiteralPath $runOllama)) {
  Write-Utf8NoBomLF $runOllama @"
import argparse, subprocess, sys
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--out-file", required=True)
    args = ap.parse_args()

    prompt_path = Path(args.prompt_file).resolve()
    out_path = Path(args.out_file).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = prompt_path.read_text(encoding="utf-8", errors="strict")
    r = subprocess.run(
        ["ollama", "run", args.model, prompt],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    text = (r.stdout or "").replace("\r\n","\n").replace("\r","\n")
    out_path.write_text(text, encoding="utf-8", newline="\n")
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "")
        return r.returncode
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"@
}

# prompts (defensive, parseable; no fences)
Write-Utf8NoBomLF (Join-Path $PDir "verify_docs_contract.txt") @"
Write tools/verify_docs_contract.py.

Requirements:
- Python stdlib only.
- CLI: python -m tools.verify_docs_contract --artifacts-dir artifacts
- Check files exist:
  docs/vision.md, docs/gates.md, docs/backlog.md, .github/pull_request_template.md
- Validate docs/backlog.md contains ALL IMP-001..IMP-040 IDs.
- Validate each docs file contains string 'MEMORY_COMMIT'.
- Stable markers:
  DOCS_CONTRACT_START
  DOCS_CONTRACT_ITEM|name=...|status=PASS/FAIL|detail=...
  DOCS_CONTRACT_SUMMARY|status=PASS/FAIL|missing=...|artifacts=...
  DOCS_CONTRACT_END
- On failure: write artifacts/docs_contract.txt and exit non-zero.
- Output ONLY file content. No code fences.
"@

Write-Utf8NoBomLF (Join-Path $PDir "verify_pr_template_contract.txt") @"
Write tools/verify_pr_template_contract.py.

Requirements:
- Python stdlib only.
- CLI: python -m tools.verify_pr_template_contract --artifacts-dir artifacts
- Validate .github/pull_request_template.md exists and contains:
  Summary, Gates, Evidence, Data hash, Code hash, Failure signals, Rollback, ANTI_REGRESSION, MEMORY_COMMIT
- Stable markers:
  PR_TEMPLATE_CONTRACT_START
  PR_TEMPLATE_CONTRACT_ITEM|name=...|status=PASS/FAIL|detail=...
  PR_TEMPLATE_CONTRACT_SUMMARY|status=PASS/FAIL|artifacts=...
  PR_TEMPLATE_CONTRACT_END
- On failure: write artifacts/pr_template_contract.txt and exit non-zero.
- Output ONLY file content. No code fences.
"@

Write-Utf8NoBomLF (Join-Path $PDir "verify_defensive_redteam.txt") @"
Write tools/verify_defensive_redteam.py.

Defensive validation only. No network.
Requirements:
- Python stdlib only.
- CLI: python -m tools.verify_defensive_redteam --repo REPO --artifacts-dir artifacts
- Verify apply_edits uses a hardcoded allowlist and rejects:
  - absolute paths (e.g., C:\\Windows\\System32\\pwn.txt)
  - path traversal (../pwn.txt)
  - allows docs/ok.md
- Ensure no env vars are printed/leaked.
- Stable markers:
  REDTEAM_START
  REDTEAM_CHECK|name=...|status=PASS/FAIL|detail=...
  REDTEAM_SUMMARY|status=PASS/FAIL|artifacts=...
  REDTEAM_END
- On failure: write artifacts/redteam.txt and exit non-zero.
- Output ONLY file content. No code fences.
"@

Write-Utf8NoBomLF (Join-Path $PDir "verify_windows_smoke.txt") @"
Write tools/verify_windows_smoke.py.

Requirements:
- Python stdlib only.
- CLI: python -m tools.verify_windows_smoke --artifacts-dir artifacts
- If not Windows: WINDOWS_SMOKE_SUMMARY|status=SKIP|reason=not_windows exit 0
- If Windows: import tools.ui_app; fail -> write artifacts/windows_smoke.txt and exit non-zero
- Stable markers:
  WINDOWS_SMOKE_START
  WINDOWS_SMOKE_STEP|name=...|status=...
  WINDOWS_SMOKE_SUMMARY|status=PASS/FAIL/SKIP|artifacts=...
  WINDOWS_SMOKE_END
- Output ONLY file content. No code fences.
"@

Write-Utf8NoBomLF (Join-Path $PDir "workflow_extra_gates.txt") @"
Write .github/workflows/extra_gates.yml.

Requirements:
- triggers: pull_request, push
- jobs:
  ubuntu-docs-and-redteam (ubuntu-latest):
    python -m tools.verify_docs_contract --artifacts-dir artifacts
    python -m tools.verify_pr_template_contract --artifacts-dir artifacts
    python -m tools.verify_defensive_redteam --repo . --artifacts-dir artifacts
    upload artifacts always
  windows-smoke (windows-latest):
    python -c ""import tools.ui_app; print('IMPORT_OK')""
    python -m tools.verify_windows_smoke --artifacts-dir artifacts
    upload artifacts always
- Minimal/deterministic. No GUI. No secrets. No network.
- Output ONLY file content. No code fences.
"@

# generate drafts via local model
& $Py -m tools.run_ollama --model $Model --prompt-file (Join-Path $PDir "verify_docs_contract.txt")        --out-file (Join-Path $DDir "verify_docs_contract.py")
& $Py -m tools.run_ollama --model $Model --prompt-file (Join-Path $PDir "verify_pr_template_contract.txt") --out-file (Join-Path $DDir "verify_pr_template_contract.py")
& $Py -m tools.run_ollama --model $Model --prompt-file (Join-Path $PDir "verify_defensive_redteam.txt")     --out-file (Join-Path $DDir "verify_defensive_redteam.py")
& $Py -m tools.run_ollama --model $Model --prompt-file (Join-Path $PDir "verify_windows_smoke.txt")        --out-file (Join-Path $DDir "verify_windows_smoke.py")
& $Py -m tools.run_ollama --model $Model --prompt-file (Join-Path $PDir "workflow_extra_gates.txt")        --out-file (Join-Path $DDir "extra_gates.yml")

# hard validate drafts + pack json via temp python script (avoid PS quoting hell)
$packPy = Join-Path $Artifacts "pack_edits_verify.py"
Write-Utf8NoBomLF $packPy @"
import json, datetime, pathlib, sys
draft = pathlib.Path(r'$DDir')
edits_path = pathlib.Path(r'$Edits')

m = {
  'tools/verify_docs_contract.py': draft/'verify_docs_contract.py',
  'tools/verify_pr_template_contract.py': draft/'verify_pr_template_contract.py',
  'tools/verify_defensive_redteam.py': draft/'verify_defensive_redteam.py',
  'tools/verify_windows_smoke.py': draft/'verify_windows_smoke.py',
  '.github/workflows/extra_gates.yml': draft/'extra_gates.yml',
}

for k,p in m.items():
  if not p.exists():
    raise SystemExit(f'missing|{p}')
  t = p.read_text(encoding='utf-8', errors='strict')
  if '```' in t:
    raise SystemExit(f'forbidden_fence|{p}')
  if len(t.strip()) < 50:
    raise SystemExit(f'too_short|{p}')

edits=[]
for k,p in m.items():
  t=p.read_text(encoding='utf-8', errors='strict').replace('\r\n','\n').replace('\r','\n')
  edits.append({'op':'FILE_WRITE','path':k,'content':t})

payload={
  'version':'1',
  'created_at': datetime.datetime.utcnow().replace(microsecond=0).isoformat()+'Z',
  'edits': edits
}

edits_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
print('BUILD_VERIFY_EDITS_OK|path=' + str(edits_path))
"@

& $Py $packPy
if ($LASTEXITCODE -ne 0) { throw "build_verify_edits_failed" }

if (-not (Test-Path -LiteralPath $Edits)) { throw "missing_output_edits_json" }

"BUILD_VERIFY_EDITS_SUMMARY|status=PASS|edits=$Edits|next=run apply_edits_v1"