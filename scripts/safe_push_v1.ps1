param(
  [string]$Remote = "origin",
  [string]$Branch = "HEAD",
  [switch]$AllowMain = $false
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "powershell_runner.ps1")

function Get-UtcTimestamp {
  return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Write-ReadyToMerge {
  param(
    [string]$Value
  )
  if ([string]::IsNullOrWhiteSpace($Value)) { $Value = "NO" }
  Write-Host ("READY_TO_MERGE=" + $Value)
}

function Write-Summary {
  param(
    [string]$Status,
    [string]$Reason,
    [string]$Next
  )
  if ([string]::IsNullOrWhiteSpace($Reason)) { $Reason = "unknown" }
  if ([string]::IsNullOrWhiteSpace($Next)) { $Next = "none" }
  Write-Host ("SAFE_PUSH_SUMMARY|status=" + $Status + "|reason=" + $Reason + "|next=" + $Next)
}

function Fail {
  param(
    [string]$Reason,
    [string]$Next
  )
  Write-ReadyToMerge -Value "NO"
  Write-Summary -Status "FAIL" -Reason $Reason -Next $Next
  Write-Host "SAFE_PUSH_END"
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

function Get-PrReadyStatus {
  param([string]$LogPath)
  if (-not (Test-Path $LogPath)) { return $null }
  $raw = Get-Content -Raw -Path $LogPath
  $m = [regex]::Match($raw, "PR_READY_SUMMARY\|status=([A-Z]+)")
  if (-not $m.Success) { return $null }
  return $m.Groups[1].Value
}

function Is-PrReadyStatusOk {
  param([string]$Status)
  if ([string]::IsNullOrWhiteSpace($Status)) { return $false }
  return @("PASS", "DEGRADED") -contains $Status
}

$ts = Get-UtcTimestamp
$cwd = (Get-Location).Path
$gitExe = Resolve-GitExe
if (-not $gitExe) {
  Write-Host ("SAFE_PUSH_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|git_exe=missing|git_version=unknown")
  Fail "git_not_found" "install_git_and_retry"
}

$gitVersion = & $gitExe --version 2>&1
$gitVersionText = ($gitVersion | Out-String).Trim()
Write-Host ("SAFE_PUSH_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|git_exe=" + $gitExe + "|git_version=" + $gitVersionText)

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

$branchResult = Run-Git -GitExe $gitExe rev-parse --abbrev-ref HEAD
if ($branchResult[0] -ne 0) {
  Fail "branch_lookup_failed" "git status"
}
$branchName = $branchResult[1]
if ($branchName -eq "main" -and -not $AllowMain) {
  Fail "refuse_push_on_main" "git checkout -b <branch>"
}

$statusResult = Run-Git -GitExe $gitExe status --porcelain
if ($statusResult[0] -ne 0) {
  Fail "git_status_failed" "git status"
}
if (-not [string]::IsNullOrWhiteSpace($statusResult[1])) {
  Fail "dirty_worktree" "git status --porcelain"
}

$env:PYTHONPATH = $repoRoot

$pythonExe = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) { $pythonExe = "python" }

New-Item -ItemType Directory -Force -Path "artifacts" | Out-Null

$prReadyLog = Join-Path "artifacts" "safe_push_verify_pr_ready.txt"

$runResult = Invoke-PsRunner -Command $pythonExe -Arguments @("-m", "tools.verify_pr_ready", "--artifacts-dir", "artifacts") -RepoRoot $repoRoot -ArtifactsDir "artifacts"
$prReadyExit = $runResult.ExitCode
$stdoutText = if (Test-Path -LiteralPath $runResult.StdoutPath) { Get-Content -Raw -LiteralPath $runResult.StdoutPath } else { "" }
$stderrText = if (Test-Path -LiteralPath $runResult.StderrPath) { Get-Content -Raw -LiteralPath $runResult.StderrPath } else { "" }
$combinedText = [string]::Concat([string]$stdoutText, [string]$stderrText).Trim()
Set-Content -LiteralPath $prReadyLog -Value $combinedText -Encoding utf8
$prReadyStatus = Get-PrReadyStatus -LogPath $prReadyLog
if (-not (Is-PrReadyStatusOk -Status $prReadyStatus)) {
  Write-Host ("SAFE_PUSH_GATE|name=verify_pr_ready|status=FAIL|log=" + $prReadyLog)
  Fail ("verify_pr_ready_status=" + ($prReadyStatus | ForEach-Object { $_ } | Out-String).Trim()) ("inspect " + $prReadyLog)
}
if ($prReadyExit -ne 0) {
  Write-Host ("SAFE_PUSH_GATE|name=verify_pr_ready|status=FAIL|log=" + $prReadyLog)
  Fail "verify_pr_ready_exit_nonzero" ("inspect " + $prReadyLog)
}
Write-Host ("SAFE_PUSH_GATE|name=verify_pr_ready|status=PASS|log=" + $prReadyLog)

$pushBranch = $Branch
if ([string]::IsNullOrWhiteSpace($pushBranch) -or $pushBranch -eq "HEAD") {
  $pushBranch = $branchName
}
$nextCommand = "git push -u " + $Remote + " " + $pushBranch

Write-ReadyToMerge -Value "YES"
Write-Summary -Status "PASS" -Reason "pr_ready" -Next $nextCommand
Write-Host "SAFE_PUSH_END"
exit 0
