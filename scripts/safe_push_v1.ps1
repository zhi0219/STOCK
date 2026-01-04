param(
  [string]$Remote = "origin",
  [string]$Branch = "HEAD"
)

$ErrorActionPreference = "Stop"

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

function Run-Gate {
  param(
    [string]$Name,
    [string]$LogPath,
    [scriptblock]$Command
  )
  $output = & $Command 2>&1 | Tee-Object -FilePath $LogPath
  $exit = $LASTEXITCODE
  if ($exit -ne 0) {
    Write-Host ("SAFE_PUSH_GATE|name=" + $Name + "|status=FAIL|log=" + $LogPath)
    Fail ("gate_failed:" + $Name) ("inspect " + $LogPath)
  }
  Write-Host ("SAFE_PUSH_GATE|name=" + $Name + "|status=PASS|log=" + $LogPath)
  return $output
}

function Get-ConsistencyStatus {
  param([string]$LogPath)
  if (-not (Test-Path $LogPath)) { return $null }
  $raw = Get-Content -Raw -Path $LogPath
  $m = [regex]::Match($raw, "CONSISTENCY_SUMMARY\|status=([A-Z]+)")
  if (-not $m.Success) { return $null }
  return $m.Groups[1].Value
}

function Is-ConsistencyStatusOk {
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
if ($branchName -eq "main") {
  Fail "refuse_push_on_main" "git checkout -b <branch>"
}

$env:PYTHONPATH = $repoRoot

$pythonExe = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) { $pythonExe = "python" }

New-Item -ItemType Directory -Force -Path "artifacts" | Out-Null

$compileLog = Join-Path "artifacts" "safe_push_compile_check.txt"
$gitHealthLog = Join-Path "artifacts" "safe_push_git_health.txt"
$foundationLog = Join-Path "artifacts" "safe_push_verify_foundation.txt"
$consistencyLog = Join-Path "artifacts" "safe_push_verify_consistency.txt"

Run-Gate -Name "compile_check" -LogPath $compileLog -Command {
  & $pythonExe -m tools.compile_check --targets tools scripts tests --artifacts-dir artifacts
}

Run-Gate -Name "git_health" -LogPath $gitHealthLog -Command {
  & $pythonExe -m tools.git_health report
}

Run-Gate -Name "verify_foundation" -LogPath $foundationLog -Command {
  & $pythonExe -m tools.verify_foundation --artifacts-dir artifacts
}

$output = & $pythonExe -m tools.verify_consistency --artifacts-dir artifacts 2>&1 | Tee-Object -FilePath $consistencyLog
$consistencyExit = $LASTEXITCODE
$consistencyStatus = Get-ConsistencyStatus -LogPath $consistencyLog
if (-not (Is-ConsistencyStatusOk -Status $consistencyStatus)) {
  Write-Host ("SAFE_PUSH_GATE|name=verify_consistency|status=FAIL|log=" + $consistencyLog)
  Fail ("verify_consistency_status=" + ($consistencyStatus | ForEach-Object { $_ } | Out-String).Trim()) ("inspect " + $consistencyLog)
}
if ($consistencyExit -ne 0) {
  Write-Host ("SAFE_PUSH_GATE|name=verify_consistency|status=FAIL|log=" + $consistencyLog)
  Fail "verify_consistency_exit_nonzero" ("inspect " + $consistencyLog)
}
Write-Host ("SAFE_PUSH_GATE|name=verify_consistency|status=PASS|log=" + $consistencyLog)

$pushLog = Join-Path "artifacts" "safe_push_git_push.txt"
$pushOut = & $gitExe push -u $Remote $Branch 2>&1 | Tee-Object -FilePath $pushLog
$pushExit = $LASTEXITCODE
if ($pushExit -ne 0) {
  Fail "git_push_failed" ("inspect " + $pushLog)
}

Write-ReadyToMerge -Value "YES"
Write-Summary -Status "PASS" -Reason "push_succeeded" -Next "monitor_ci"
Write-Host "SAFE_PUSH_END"
exit 0
