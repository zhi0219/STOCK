param(
  [string]$PatchBotOut = "",
  [string]$Confirm = "",
  [int]$MaxFiles = 10,
  [int]$MaxAddedRemovedLines = 300,
  [string[]]$AllowRootPrefixes = @("tools/","scripts/","docs/",".github/"),
  [switch]$AllowAnyPath
)

$ErrorActionPreference = "Stop"

function Fail([string]$msg) {

  # PATCHBOT_IGNORE_START:git_checkout_message
  # Non-fatal: git sometimes prints this on stderr on Windows while exitcode=0
  $msg = $null
  try { $gv = Get-Variable -Name reason -ErrorAction SilentlyContinue; if ($gv) { $msg = $gv.Value } } catch {}
  if ($null -eq $msg -and $args.Count -ge 1) { $msg = $args[0] }
  if ($msg -is [string]) {
    if ($msg.StartsWith("Switched to a new branch ")) {
      Write-Host ("WRAP_NOTE|nonfatal_git_message=" + $msg)
      return
    }
  }
  # PATCHBOT_IGNORE_END:git_checkout_message


  Write-Host "WRAP_SUMMARY|status=FAIL|reason=$msg"
  exit 1
}

# Resolve real git.exe path (avoid alias/function shadowing)
$gitCmd = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $gitCmd -or -not $gitCmd.Source) { Fail "git_exe_not_found" }
$gitExe = $gitCmd.Source

function GitOK {
  param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)
  if (-not $Rest -or $Rest.Count -eq 0) { Fail "git_args_empty" }
  $out = & $gitExe @Rest 2>&1
  $code = $LASTEXITCODE
  $txt = ($out | Out-String)
  if ($code -ne 0) { Fail ("git_failed:" + ($Rest -join " ") + "|" + $txt.Trim()) }
  return $txt
}

function PickLatestPatchBotOut {
  $f = Get-ChildItem -Path "artifacts" -Filter "patch_bot_*.txt" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if (-not $f) { Fail "no_patch_bot_output_found_in_artifacts" }
  return $f.FullName
}

function ExtractPatchSection([string]$raw) {
  $m = [regex]::Match($raw, '(?ms)^\[PATCH\]\s*(.+?)(?=^\[(NEW_OR_UPDATED_GATES|ROLLBACK|ASSUMPTIONS)\]\s*$)')
  if ($m.Success) { return $m.Groups[1].Value.Trim() }
  $m2 = [regex]::Match($raw, '(?ms)^\[PATCH\]\s*(.+)$')
  if ($m2.Success) { return $m2.Groups[1].Value.Trim() }
  return $null
}

function StripCodeFences([string]$p) {
  $lines = $p -split "`r?`n"
  $lines2 = $lines | Where-Object { $_ -notmatch '^\s*```' }
  return ($lines2 -join "`n").Trim()
}

function RollbackAndDeleteBranch([string]$origBranch, [string]$branch) {
  try { & $gitExe reset --hard HEAD 2>$null | Out-Null } catch {}
  try { & $gitExe checkout $origBranch 2>$null | Out-Null } catch {}
  try { & $gitExe branch -D $branch 2>$null | Out-Null } catch {}
}

# Repo root + clean tracked tree only (ignore ?? prompts/work/etc)
$repo = (GitOK rev-parse --show-toplevel).Trim()
Set-Location $repo

$trackedDirty = (GitOK status --porcelain --untracked-files=no).Trim()
if ($trackedDirty.Length -ne 0) { Fail "worktree_not_clean_tracked_changes_present" }

$origBranch = (GitOK rev-parse --abbrev-ref HEAD).Trim()

if ([string]::IsNullOrWhiteSpace($PatchBotOut)) { $PatchBotOut = PickLatestPatchBotOut }
if (-not (Test-Path $PatchBotOut)) { Fail "missing_patch_bot_out" }

$raw = Get-Content $PatchBotOut -Raw
$patch = ExtractPatchSection $raw
if (-not $patch) { Fail "missing_PATCH_section" }

$patch = StripCodeFences $patch
if (-not ($patch -match '(?m)^(diff --git|---\s|\+\+\+\s)')) { Fail "patch_section_not_unified_diff" }

# changed files
$files = New-Object System.Collections.Generic.List[string]
foreach ($m in [regex]::Matches($patch, '(?m)^diff --git a\/(.+?) b\/(.+?)$')) {
  $files.Add($m.Groups[2].Value)
}
$files = $files | Select-Object -Unique
if ($files.Count -eq 0) { Fail "could_not_extract_changed_files" }
if ($files.Count -gt $MaxFiles) { Fail ("too_many_files:" + $files.Count) }

# path allowlist
if (-not $AllowAnyPath) {
  foreach ($f in $files) {
    $norm = $f.Replace("\","/")
    $ok = $false
    foreach ($pfx in $AllowRootPrefixes) {
      if ($norm.StartsWith($pfx)) { $ok = $true; break }
    }
    if (-not $ok) { Fail ("path_out_of_allowlist:" + $f) }
  }
}

# size check
$adds = ([regex]::Matches($patch, '(?m)^\+(?!\+\+\+).*')).Count
$dels = ([regex]::Matches($patch, '(?m)^-(?!---).*')).Count
$delta = $adds + $dels
if ($delta -gt $MaxAddedRemovedLines) { Fail ("patch_too_large_lines:" + $delta) }

# write patch file
New-Item -ItemType Directory -Force -Path "artifacts" | Out-Null
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$patchFile = Join-Path "artifacts" ("patch_apply_" + $ts + ".patch")
[IO.File]::WriteAllText($patchFile, $patch, (New-Object System.Text.UTF8Encoding($false)))

$mode = if ($Confirm -eq "APPLY") { "APPLY" } else { "DRY_RUN" }
Write-Host "WRAP_START|git_exe=$gitExe|patch_bot_out=$PatchBotOut|patch_file=$patchFile|files=$($files.Count)|delta_lines=$delta|mode=$mode"
Write-Host ("WRAP_FILES_START`n" + ($files -join "`n") + "`nWRAP_FILES_END")
Write-Host "WRAP_SUMMARY|status=PASS|dry_run_ready=1|confirm_to_apply=APPLY"

if ($Confirm -ne "APPLY") { exit 0 }

# APPLY (fail-closed: rollback + delete branch on any error)
$branch = ("patchbot/" + $ts)

try {
$null = & $gitExe checkout -b $branch 2>&1; if ($LASTEXITCODE -ne 0) { throw ("git_checkout_branch_failed|" + $LASTEXITCODE) }

  $o1 = & $gitExe apply --check $patchFile 2>&1
  if ($LASTEXITCODE -ne 0) { throw ("git_apply_check_failed|" + (($o1 | Out-String).Trim())) }

  $o2 = & $gitExe apply $patchFile 2>&1
  if ($LASTEXITCODE -ne 0) { throw ("git_apply_failed|" + (($o2 | Out-String).Trim())) }

  $py = ".\.venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { $py = "python" }

  $i = & $py -c "import tools.ui_app; print('IMPORT_OK')" 2>&1
  if ($LASTEXITCODE -ne 0) { throw ("import_smoke_failed|" + (($i | Out-String).Trim())) }

  $c = & $py -m tools.compile_check --targets tools scripts --artifacts-dir artifacts 2>&1
  if ($LASTEXITCODE -ne 0) { throw ("compile_check_failed|" + (($c | Out-String).Trim())) }

  foreach ($f in $files) {
    if (Test-Path $f) { & $gitExe add -- $f | Out-Null }
    else { & $gitExe add -u -- $f | Out-Null }
  }

  $st = (GitOK diff --cached --name-only).Trim()
  if ($st.Length -eq 0) { throw "nothing_staged_after_apply" }

  $msg = "PATCHBOT: apply minimal fix + gates`n`nSource: $([System.IO.Path]::GetFileName($PatchBotOut))`nPatch: $([System.IO.Path]::GetFileName($patchFile))"
  $cm = & $gitExe commit -m $msg 2>&1
  if ($LASTEXITCODE -ne 0) { throw ("commit_failed|" + (($cm | Out-String).Trim())) }

  Write-Host "WRAP_END|branch=$branch|commit_created=1"
  Write-Host "WRAP_SUMMARY|status=PASS|branch=$branch|next=push_and_open_pr_manually"
  exit 0
}
catch {
  $reason = $_.Exception.Message
  RollbackAndDeleteBranch $origBranch $branch
  Fail $reason
}