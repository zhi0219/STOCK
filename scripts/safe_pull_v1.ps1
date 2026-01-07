param(
  [string]$Remote = "",
  [string]$Branch = "",
  [string]$AutoStash = "NO"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "powershell_runner.ps1")

$script:AutoStashSummary = $null
$script:AutoStashLog = $null
$script:AutoStashArtifactsDir = ""
$script:AutoStashEmitted = $false
$script:AutoStashTimestamp = ""

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

function Emit-AutoStashIfReady {
  if ($script:AutoStashEmitted) { return }
  if (-not $script:AutoStashSummary) { return }
  if ([string]::IsNullOrWhiteSpace($script:AutoStashArtifactsDir)) { return }

  $logPath = Join-Path $script:AutoStashArtifactsDir "safe_pull_autostash.txt"
  $summaryPath = Join-Path $script:AutoStashArtifactsDir "safe_pull_autostash.json"
  $logText = if ($script:AutoStashLog -and $script:AutoStashLog.Count -gt 0) { $script:AutoStashLog -join "`n" } else { "" }
  Set-Content -LiteralPath $logPath -Value $logText -Encoding utf8
  $summaryJson = $script:AutoStashSummary | ConvertTo-Json -Depth 6
  Set-Content -LiteralPath $summaryPath -Value $summaryJson -Encoding utf8

  Write-Host ("SAFE_PULL_AUTOSTASH_START|ts_utc=" + $script:AutoStashTimestamp + "|autostash_enabled=" + $script:AutoStashSummary.autostash_enabled + "|was_dirty=" + $script:AutoStashSummary.was_dirty)
  Write-Host ("SAFE_PULL_AUTOSTASH_SUMMARY|status=" + $script:AutoStashSummary.status + "|stash_created=" + $script:AutoStashSummary.stash_created + "|pull_status=" + $script:AutoStashSummary.pull_status + "|rollback_status=" + $script:AutoStashSummary.rollback_status + "|next=" + $script:AutoStashSummary.next)
  Write-Host "SAFE_PULL_AUTOSTASH_END"

  $script:AutoStashEmitted = $true
}

function Fail {
  param(
    [string]$Reason,
    [string]$Next
  )
  Emit-AutoStashIfReady
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
    [string]$RepoRoot = "",
    [string]$ArtifactsDir = "",
    [string]$MarkerPrefix = "SAFE_PULL_GIT",
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
  )
  $runResult = Invoke-PsRunner -Command $GitExe -Arguments $Args -RepoRoot $RepoRoot -ArtifactsDir $ArtifactsDir -MarkerPrefix $MarkerPrefix
  $stdoutText = ""
  if (Test-Path -LiteralPath $runResult.StdoutPath) {
    try {
      $stdoutText = Get-Content -Raw -LiteralPath $runResult.StdoutPath -ErrorAction Stop
    } catch {
      Fail "git_stdout_read_failed" ("inspect_ps_runner_artifacts:" + $runResult.StdoutPath)
    }
  }
  if ($null -eq $stdoutText) { $stdoutText = "" }
  $stderrText = ""
  if (Test-Path -LiteralPath $runResult.StderrPath) {
    try {
      $stderrText = Get-Content -Raw -LiteralPath $runResult.StderrPath -ErrorAction Stop
    } catch {
      Fail "git_stderr_read_failed" ("inspect_ps_runner_artifacts:" + $runResult.StderrPath)
    }
  }
  if ($null -eq $stderrText) { $stderrText = "" }
  $combinedText = [string]::Concat($stdoutText, $stderrText).Trim()
  return @($runResult.ExitCode, $combinedText)
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
$script:AutoStashTimestamp = $ts
$cwd = (Get-Location).Path
$gitExe = Resolve-GitExe
if (-not $gitExe) {
  Write-Host ("SAFE_PULL_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|git_exe=missing|git_version=unknown")
  Fail "git_not_found" "install_git_and_retry"
}

$autoStashValue = if ($AutoStash) { $AutoStash.Trim().ToUpperInvariant() } else { "NO" }
if (-not (@("YES", "NO") -contains $autoStashValue)) {
  Write-Host ("SAFE_PULL_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|git_exe=" + $gitExe + "|git_version=unknown")
  Fail "invalid_autostash" "set -AutoStash YES|NO"
}
$autoStashEnabled = $autoStashValue -eq "YES"

$initialArtifactsDir = Join-Path $cwd "artifacts"
$gitVersionResult = Run-Git -GitExe $gitExe -RepoRoot $cwd -ArtifactsDir $initialArtifactsDir -MarkerPrefix "SAFE_PULL_GIT_VERSION" --version
$gitVersionText = if ($gitVersionResult[0] -eq 0 -and -not [string]::IsNullOrWhiteSpace($gitVersionResult[1])) { $gitVersionResult[1] } else { "unknown" }
Write-Host ("SAFE_PULL_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|git_exe=" + $gitExe + "|git_version=" + $gitVersionText)

$rootResult = Run-Git -GitExe $gitExe -RepoRoot $cwd -ArtifactsDir $initialArtifactsDir -MarkerPrefix "SAFE_PULL_REV_PARSE" rev-parse --show-toplevel
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

$artifactsDir = Join-Path $repoRoot "artifacts"
New-Item -ItemType Directory -Force -Path $artifactsDir | Out-Null

$script:AutoStashArtifactsDir = $artifactsDir
$script:AutoStashLog = New-Object System.Collections.Generic.List[string]
$script:AutoStashSummary = [ordered]@{
  status = "SKIPPED"
  was_dirty = $false
  autostash_enabled = $autoStashEnabled
  stash_created = $false
  stash_ref = ""
  pull_status = "SKIPPED"
  rollback_attempted = $false
  rollback_status = "not_attempted"
  next = "none"
}

$statusResult = Run-Git -GitExe $gitExe -RepoRoot $repoRoot -ArtifactsDir $artifactsDir -MarkerPrefix "SAFE_PULL_STATUS" status --porcelain
if ($statusResult[0] -ne 0) {
  Fail "git_status_failed" "git status"
}
if (-not [string]::IsNullOrWhiteSpace($statusResult[1])) {
  $script:AutoStashSummary.was_dirty = $true
  if (-not $autoStashEnabled) {
    $script:AutoStashSummary.status = "FAIL"
    $script:AutoStashSummary.next = "git status --porcelain"
    Fail "dirty_worktree" "git status --porcelain"
  }

  $stashMessage = "auto_pre_safe_pull_" + $ts
  $script:AutoStashLog.Add("stash_push_command=git stash push -u -m " + $stashMessage)
  $stashResult = Invoke-PsRunner -Command $gitExe -Arguments @("stash", "push", "-u", "-m", $stashMessage) -RepoRoot $repoRoot -ArtifactsDir $artifactsDir -MarkerPrefix "SAFE_PULL_AUTOSTASH_PUSH"
  $stashStdout = if (Test-Path -LiteralPath $stashResult.StdoutPath) { Get-Content -Raw -LiteralPath $stashResult.StdoutPath } else { "" }
  if ($null -eq $stashStdout) { $stashStdout = "" }
  $stashStderr = if (Test-Path -LiteralPath $stashResult.StderrPath) { Get-Content -Raw -LiteralPath $stashResult.StderrPath } else { "" }
  if ($null -eq $stashStderr) { $stashStderr = "" }
  $stashCombined = [string]::Concat($stashStdout, $stashStderr).Trim()
  $script:AutoStashLog.Add("stash_push_output=" + $stashCombined)

  if ($stashResult.ExitCode -ne 0) {
    $script:AutoStashSummary.status = "FAIL"
    $script:AutoStashSummary.next = "inspect " + (Join-Path $artifactsDir "safe_pull_autostash.txt")
    Fail "git_stash_failed" ("inspect " + (Join-Path $artifactsDir "safe_pull_autostash.txt"))
  }

  $script:AutoStashSummary.stash_created = $true
  $stashMatch = [regex]::Match($stashCombined, "stash@\{\d+\}")
  if ($stashMatch.Success) {
    $script:AutoStashSummary.stash_ref = $stashMatch.Value
  }

  $statusResult = Run-Git -GitExe $gitExe -RepoRoot $repoRoot -ArtifactsDir $artifactsDir -MarkerPrefix "SAFE_PULL_STATUS_POST_STASH" status --porcelain
  if ($statusResult[0] -ne 0) {
    $script:AutoStashSummary.status = "FAIL"
    $script:AutoStashSummary.next = "git status"
    Fail "git_status_failed" "git status"
  }
  if (-not [string]::IsNullOrWhiteSpace($statusResult[1])) {
    $script:AutoStashSummary.status = "FAIL"
    $script:AutoStashSummary.next = "git status --porcelain"
    Fail "dirty_worktree_after_autostash" "git status --porcelain"
  }
}

$unmergedResult = Run-Git -GitExe $gitExe -RepoRoot $repoRoot -ArtifactsDir $artifactsDir -MarkerPrefix "SAFE_PULL_LS_FILES" ls-files -u
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

$pullArgs = @("pull", "--ff-only")
# Contract trace: git pull --ff-only
if (-not [string]::IsNullOrWhiteSpace($Remote)) { $pullArgs += $Remote }
if (-not [string]::IsNullOrWhiteSpace($Branch)) { $pullArgs += $Branch }

$runResult = Invoke-PsRunner -Command $gitExe -Arguments $pullArgs -RepoRoot $repoRoot -ArtifactsDir $artifactsDir -MarkerPrefix "SAFE_PULL_GIT_PULL"
$stdoutText = if (Test-Path -LiteralPath $runResult.StdoutPath) { Get-Content -Raw -LiteralPath $runResult.StdoutPath } else { "" }
if ($null -eq $stdoutText) { $stdoutText = "" }
$stderrText = if (Test-Path -LiteralPath $runResult.StderrPath) { Get-Content -Raw -LiteralPath $runResult.StderrPath } else { "" }
if ($null -eq $stderrText) { $stderrText = "" }
$combinedText = [string]::Concat($stdoutText, $stderrText).Trim()
$pullLog = Join-Path $artifactsDir "safe_pull_git_pull.txt"
Set-Content -LiteralPath $pullLog -Value $combinedText -Encoding utf8

if ($runResult.ExitCode -ne 0) {
  $script:AutoStashSummary.pull_status = "FAIL"
  if ($script:AutoStashSummary.stash_created) {
    $script:AutoStashSummary.rollback_attempted = $true
    $script:AutoStashLog.Add("stash_pop_command=git stash pop")
    $rollbackResult = Invoke-PsRunner -Command $gitExe -Arguments @("stash", "pop") -RepoRoot $repoRoot -ArtifactsDir $artifactsDir -MarkerPrefix "SAFE_PULL_AUTOSTASH_POP"
    $rollbackStdout = if (Test-Path -LiteralPath $rollbackResult.StdoutPath) { Get-Content -Raw -LiteralPath $rollbackResult.StdoutPath } else { "" }
    if ($null -eq $rollbackStdout) { $rollbackStdout = "" }
    $rollbackStderr = if (Test-Path -LiteralPath $rollbackResult.StderrPath) { Get-Content -Raw -LiteralPath $rollbackResult.StderrPath } else { "" }
    if ($null -eq $rollbackStderr) { $rollbackStderr = "" }
    $rollbackCombined = [string]::Concat($rollbackStdout, $rollbackStderr).Trim()
    $script:AutoStashLog.Add("stash_pop_output=" + $rollbackCombined)
    $script:AutoStashSummary.rollback_status = if ($rollbackResult.ExitCode -eq 0) { "PASS" } else { "FAIL" }
  }

  if ($script:AutoStashSummary.autostash_enabled -and $script:AutoStashSummary.stash_created) {
    $script:AutoStashSummary.status = "FAIL"
    $script:AutoStashSummary.next = "inspect " + $pullLog
  }
  Fail "git_pull_ff_only_failed" ("inspect " + $pullLog)
}

$script:AutoStashSummary.pull_status = "PASS"
$nextAction = "none"
if ($script:AutoStashSummary.stash_created) {
  $script:AutoStashSummary.status = "PASS"
  $nextAction = "stash preserved (git stash list; git stash pop when ready)"
  $script:AutoStashSummary.next = $nextAction
}

Emit-AutoStashIfReady
Write-Summary -Status "PASS" -Reason "fast_forward" -Next $nextAction
Write-Host "SAFE_PULL_END"
exit 0
