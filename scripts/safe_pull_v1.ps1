param(
  [string]$Remote = "",
  [string]$Branch = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "powershell_runner.ps1")

function Get-UtcTimestamp {
  return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Write-Summary {
  param(
    [string]$Status,
    [string]$Reason,
    [string]$Next
  )
  if ([string]::IsNullOrWhiteSpace($Reason)) { $Reason = "unknown" }
  if ([string]::IsNullOrWhiteSpace($Next)) { $Next = "none" }
  Write-Host ("SAFE_PULL_SUMMARY|status=" + $Status + "|reason=" + $Reason + "|next=" + $Next)
}

function Fail {
  param(
    [string]$Reason,
    [string]$Next
  )
  Write-Summary -Status "FAIL" -Reason $Reason -Next $Next
  Write-Host "SAFE_PULL_END"
  exit 1
}

function Resolve-GitExe {
  $gitCmd = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $gitCmd -or -not $gitCmd.Source) { return $null }
  return $gitCmd.Source
}

function Run-Git {
  param(
    [string]$GitExe,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
  )
  $out = & $GitExe @Args 2>&1
  $code = $LASTEXITCODE
  $txt = ($out | Out-String)
  return @($code, $txt.Trim())
}

function Resolve-GitStateBlocks {
  param(
    [string]$RepoRoot
  )
  $gitDir = Join-Path $RepoRoot ".git"
  $statePaths = @(
    (Join-Path $gitDir "MERGE_HEAD"),
    (Join-Path $gitDir "CHERRY_PICK_HEAD"),
    (Join-Path $gitDir "REVERT_HEAD"),
    (Join-Path $gitDir "rebase-apply"),
    (Join-Path $gitDir "rebase-merge"),
    (Join-Path $gitDir "AM")
  )
  $blocked = New-Object System.Collections.Generic.List[string]
  foreach ($path in $statePaths) {
    if (Test-Path -LiteralPath $path) {
      $blocked.Add([IO.Path]::GetFileName($path))
    }
  }
  return $blocked
}

$ts = Get-UtcTimestamp
$cwd = (Get-Location).Path
$gitExe = Resolve-GitExe
if (-not $gitExe) {
  Write-Host ("SAFE_PULL_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|git_exe=missing|git_version=unknown")
  Fail "git_not_found" "install_git_and_retry"
}

$gitVersion = & $gitExe --version 2>&1
$gitVersionText = ($gitVersion | Out-String).Trim()
Write-Host ("SAFE_PULL_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|git_exe=" + $gitExe + "|git_version=" + $gitVersionText)

$rootResult = Run-Git -GitExe $gitExe rev-parse --show-toplevel
if ($rootResult[0] -ne 0) {
  Fail "not_in_git_repo" "cd <repo_root>"
}
$repoRoot = $rootResult[1]
$cwdFull = [IO.Path]::GetFullPath($cwd)
$repoFull = [IO.Path]::GetFullPath($repoRoot)
if ($cwdFull -ne $repoFull) {
  Fail "not_at_repo_root" ("cd " + $repoRoot)
}
$repoRoot = $repoFull

$statusResult = Run-Git -GitExe $gitExe status --porcelain
if ($statusResult[0] -ne 0) {
  Fail "git_status_failed" "git status"
}
if (-not [string]::IsNullOrWhiteSpace($statusResult[1])) {
  Fail "dirty_worktree" "git status --porcelain"
}

$unmergedResult = Run-Git -GitExe $gitExe ls-files -u
if ($unmergedResult[0] -ne 0) {
  Fail "git_unmerged_check_failed" "git ls-files -u"
}
if (-not [string]::IsNullOrWhiteSpace($unmergedResult[1])) {
  Fail "unmerged_paths" "resolve_unmerged_paths"
}

$blockedStates = Resolve-GitStateBlocks -RepoRoot $repoRoot
if ($blockedStates.Count -gt 0) {
  $blockedText = ($blockedStates | Sort-Object) -join ","
  Fail ("git_state_present:" + $blockedText) "resolve_git_state_then_retry"
}

$artifactsDir = Join-Path $repoRoot "artifacts"
New-Item -ItemType Directory -Force -Path $artifactsDir | Out-Null

$pullArgs = @("pull", "--ff-only")
# Contract trace: git pull --ff-only
if (-not [string]::IsNullOrWhiteSpace($Remote)) { $pullArgs += $Remote }
if (-not [string]::IsNullOrWhiteSpace($Branch)) { $pullArgs += $Branch }

$runResult = Invoke-PsRunner -Command $gitExe -Arguments $pullArgs -RepoRoot $repoRoot -ArtifactsDir $artifactsDir -MarkerPrefix "PS_RUN"
$stdoutText = if (Test-Path -LiteralPath $runResult.StdoutPath) { Get-Content -Raw -LiteralPath $runResult.StdoutPath } else { "" }
$stderrText = if (Test-Path -LiteralPath $runResult.StderrPath) { Get-Content -Raw -LiteralPath $runResult.StderrPath } else { "" }
$combinedText = ($stdoutText + $stderrText).Trim()
$pullLog = Join-Path $artifactsDir "safe_pull_git_pull.txt"
Set-Content -LiteralPath $pullLog -Value $combinedText -Encoding utf8

if ($runResult.ExitCode -ne 0) {
  Fail "git_pull_ff_only_failed" ("inspect " + $pullLog)
}

Write-Summary -Status "PASS" -Reason "fast_forward" -Next "none"
Write-Host "SAFE_PULL_END"
exit 0
