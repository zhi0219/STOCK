param(
  [string]$RepoRoot = "",
  [string]$ArtifactsDir = "",
  [string]$AutoStash = "YES"
)

$ErrorActionPreference = "Stop"

function Get-UtcTimestamp {
  return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Write-DailyLog {
  param(
    [string]$Message,
    [string]$IsError = "NO"
  )
  $line = if ($Message) { [string]$Message } else { "" }
  if ($IsError -eq "YES") {
    Add-Content -LiteralPath $dailyErrPath -Value $line -Encoding utf8
    return
  }
  Add-Content -LiteralPath $dailyOutPath -Value $line -Encoding utf8
}

function Fail-DailyGreen {
  param(
    [string]$FailedStep,
    [string]$Next
  )
  if ([string]::IsNullOrWhiteSpace($FailedStep)) { $FailedStep = "unknown" }
  if ([string]::IsNullOrWhiteSpace($Next)) { $Next = "none" }
  $runDirValue = if ([string]::IsNullOrWhiteSpace($script:DailyGreenRunDir)) { "unknown" } else { $script:DailyGreenRunDir }
  Write-Host ("DAILY_GREEN_SUMMARY|status=FAIL|failed_step=" + $FailedStep + "|next=" + $Next + "|run_dir=" + $runDirValue)
  Write-Host "DAILY_GREEN_END"
  exit 1
}

function Resolve-PythonExe {
  param(
    [string]$RepoRoot
  )
  $venvRoot = Join-Path $RepoRoot ".venv"
  $venvPython = Join-Path (Join-Path $venvRoot "Scripts") "python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return [IO.Path]::GetFullPath($venvPython)
  }
  $pythonCmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pythonCmd -and $pythonCmd.Source) {
    return $pythonCmd.Source
  }
  return $null
}

function Resolve-GitExe {
  $gitCmd = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $gitCmd -or -not $gitCmd.Source) { return $null }
  return $gitCmd.Source
}

function Invoke-DailyStep {
  param(
    [string]$Name,
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory,
    [string]$StepDir
  )
  if (-not (Test-Path -LiteralPath $StepDir)) {
    New-Item -ItemType Directory -Force -Path $StepDir | Out-Null
  }
  $stdoutPath = Join-Path $StepDir ($Name + "_stdout.txt")
  $stderrPath = Join-Path $StepDir ($Name + "_stderr.txt")
  Set-Content -LiteralPath $stdoutPath -Value "" -Encoding utf8
  Set-Content -LiteralPath $stderrPath -Value "" -Encoding utf8
  $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
  $exitCode = $process.ExitCode
  $status = if ($exitCode -eq 0) { "PASS" } else { "FAIL" }
  Write-Host ("DAILY_GREEN_STEP|name=" + $Name + "|status=" + $status + "|exit_code=" + $exitCode + "|stdout=" + $stdoutPath + "|stderr=" + $stderrPath)
  return [PSCustomObject]@{
    ExitCode = $exitCode
    Status = $status
    StdoutPath = $stdoutPath
    StderrPath = $stderrPath
  }
}

$ts = Get-UtcTimestamp
$cwd = (Get-Location).Path

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  $RepoRoot = $cwd
}
if ([string]::IsNullOrWhiteSpace($ArtifactsDir)) {
  Write-Host "DAILY_GREEN_START|status=FAIL|reason=missing_artifacts_dir"
  Fail-DailyGreen -FailedStep "preflight_artifacts_dir" -Next "set -ArtifactsDir <path>"
}

$repoRootFull = ""
if (Test-Path -LiteralPath $RepoRoot) {
  $repoRootFull = [IO.Path]::GetFullPath($RepoRoot)
}
if ([string]::IsNullOrWhiteSpace($repoRootFull)) {
  Write-Host "DAILY_GREEN_START|status=FAIL|reason=repo_root_missing"
  Fail-DailyGreen -FailedStep "preflight_repo_root" -Next "set -RepoRoot <path>"
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRootFull ".git"))) {
  Write-Host "DAILY_GREEN_START|status=FAIL|reason=missing_git_dir"
  Fail-DailyGreen -FailedStep "preflight_git_root" -Next "set -RepoRoot <path with .git>"
}

$artifactsRoot = if ([IO.Path]::IsPathRooted($ArtifactsDir)) { $ArtifactsDir } else { Join-Path $repoRootFull $ArtifactsDir }
$artifactsRoot = [IO.Path]::GetFullPath($artifactsRoot)
$runStamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmssZ")
$runDir = Join-Path $artifactsRoot $runStamp
$script:DailyGreenRunDir = $runDir

New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$dailyOutPath = Join-Path $runDir "daily_green_out.txt"
$dailyErrPath = Join-Path $runDir "daily_green_err.txt"
Set-Content -LiteralPath $dailyOutPath -Value "" -Encoding utf8
Set-Content -LiteralPath $dailyErrPath -Value "" -Encoding utf8

$autoStashValue = if ($AutoStash) { [string]$AutoStash } else { "YES" }
$autoStashValue = [string]::Concat($autoStashValue, "").Trim().ToUpperInvariant()
if (-not (@("YES", "NO") -contains $autoStashValue)) {
  Write-Host ("DAILY_GREEN_START|ts_utc=" + $ts + "|repo_root=" + $repoRootFull + "|artifacts_dir=" + $artifactsRoot + "|run_dir=" + $runDir)
  Fail-DailyGreen -FailedStep "preflight_autostash" -Next "set -AutoStash YES|NO"
}

$pythonExe = Resolve-PythonExe -RepoRoot $repoRootFull
if (-not $pythonExe) {
  Write-Host ("DAILY_GREEN_START|ts_utc=" + $ts + "|repo_root=" + $repoRootFull + "|artifacts_dir=" + $artifactsRoot + "|run_dir=" + $runDir + "|python=missing")
  Fail-DailyGreen -FailedStep "preflight_python" -Next "install_python_and_retry"
}

Write-Host ("DAILY_GREEN_START|ts_utc=" + $ts + "|repo_root=" + $repoRootFull + "|artifacts_dir=" + $artifactsRoot + "|run_dir=" + $runDir + "|python=" + $pythonExe + "|autostash=" + $autoStashValue)

$gitExe = Resolve-GitExe
if (-not $gitExe) {
  Write-DailyLog -Message "git_not_found" -IsError "YES"
  Fail-DailyGreen -FailedStep "preflight_git" -Next "install_git_and_retry"
}

$gitPreflight = Invoke-DailyStep -Name "git_status_pre" -FilePath $gitExe -Arguments @("status", "--porcelain") -WorkingDirectory $repoRootFull -StepDir $runDir
if ($gitPreflight.ExitCode -ne 0) {
  Write-DailyLog -Message "git status failed (preflight)" -IsError "YES"
  Fail-DailyGreen -FailedStep "preflight_clean_worktree" -Next "git status --porcelain"
}
$preStdout = if (Test-Path -LiteralPath $gitPreflight.StdoutPath) { Get-Content -Raw -LiteralPath $gitPreflight.StdoutPath } else { "" }
$preStderr = if (Test-Path -LiteralPath $gitPreflight.StderrPath) { Get-Content -Raw -LiteralPath $gitPreflight.StderrPath } else { "" }
$preCombined = [string]::Concat([string]$preStdout, [string]$preStderr)
$preCombined = $preCombined.Trim()
if (-not [string]::IsNullOrWhiteSpace($preCombined)) {
  Write-DailyLog -Message "dirty worktree preflight" -IsError "YES"
  Fail-DailyGreen -FailedStep "preflight_clean_worktree" -Next "git status --porcelain"
}

$safePullDir = Join-Path $runDir "safe_pull"
$safePullScript = Join-Path $PSScriptRoot "safe_pull_v1.ps1"
$safePullArgs = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $safePullScript,
  "-ArtifactsDir",
  $safePullDir,
  "-AutoStash",
  $autoStashValue
)
$safePullStep = Invoke-DailyStep -Name "safe_pull" -FilePath "powershell.exe" -Arguments $safePullArgs -WorkingDirectory $repoRootFull -StepDir $safePullDir
if ($safePullStep.ExitCode -ne 0) {
  Write-DailyLog -Message "safe_pull failed" -IsError "YES"
  Fail-DailyGreen -FailedStep "safe_pull" -Next ("inspect " + $safePullStep.StdoutPath)
}

$repoDoctorDir = Join-Path $runDir "repo_doctor"
$repoDoctorScript = Join-Path $PSScriptRoot "repo_doctor_v1.ps1"
$repoDoctorArgs = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $repoDoctorScript,
  "-RepoRoot",
  $repoRootFull,
  "-ArtifactsDir",
  $repoDoctorDir,
  "-WriteDocs",
  "NO",
  "-PythonExe",
  $pythonExe
)
$repoDoctorStep = Invoke-DailyStep -Name "repo_doctor" -FilePath "powershell.exe" -Arguments $repoDoctorArgs -WorkingDirectory $repoRootFull -StepDir $repoDoctorDir
if ($repoDoctorStep.ExitCode -ne 0) {
  Write-DailyLog -Message "repo_doctor failed" -IsError "YES"
  Fail-DailyGreen -FailedStep "repo_doctor" -Next ("inspect " + $repoDoctorStep.StdoutPath)
}

$gitPostflight = Invoke-DailyStep -Name "git_status_post" -FilePath $gitExe -Arguments @("status", "--porcelain") -WorkingDirectory $repoRootFull -StepDir $runDir
if ($gitPostflight.ExitCode -ne 0) {
  Write-DailyLog -Message "git status failed (postflight)" -IsError "YES"
  Fail-DailyGreen -FailedStep "postflight_clean_worktree" -Next "git diff --name-only"
}
$postStdout = if (Test-Path -LiteralPath $gitPostflight.StdoutPath) { Get-Content -Raw -LiteralPath $gitPostflight.StdoutPath } else { "" }
$postStderr = if (Test-Path -LiteralPath $gitPostflight.StderrPath) { Get-Content -Raw -LiteralPath $gitPostflight.StderrPath } else { "" }
$postCombined = [string]::Concat([string]$postStdout, [string]$postStderr)
$postCombined = $postCombined.Trim()
if (-not [string]::IsNullOrWhiteSpace($postCombined)) {
  Write-DailyLog -Message "dirty worktree postflight" -IsError "YES"
  Fail-DailyGreen -FailedStep "postflight_clean_worktree" -Next "git diff --name-only"
}

Write-DailyLog -Message "daily green pass"
Write-Host ("DAILY_GREEN_SUMMARY|status=PASS|failed_step=none|next=none|run_dir=" + $runDir)
Write-Host "DAILY_GREEN_END"
exit 0
