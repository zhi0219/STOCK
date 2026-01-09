param(
  [string]$RepoRoot = "",
  [string]$ArtifactsDir = "",
  [string]$WriteDocs = "NO",
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "powershell_runner.ps1")

function Get-UtcTimestamp {
  return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Write-RepoDoctorSummary {
  param(
    [string]$Status,
    [string]$FailedStep,
    [string]$Next
  )
  if ([string]::IsNullOrWhiteSpace($FailedStep)) { $FailedStep = "none" }
  if ([string]::IsNullOrWhiteSpace($Next)) { $Next = "none" }

  $summary = [ordered]@{
    status = $Status
    failed_step = $FailedStep
    next = $Next
    ts_utc = $script:RepoDoctorTimestamp
    repo_root = $script:RepoDoctorRepoRoot
    artifacts_dir = $script:RepoDoctorArtifactsDir
  }

  $summaryPath = Join-Path $script:RepoDoctorArtifactsDir "repo_doctor_summary.json"
  $summaryJson = $summary | ConvertTo-Json -Depth 6
  Set-Content -LiteralPath $summaryPath -Value $summaryJson -Encoding utf8

  if ($script:RepoDoctorSteps -and $script:RepoDoctorSteps.Count -gt 0) {
    $stepsPath = Join-Path $script:RepoDoctorArtifactsDir "repo_doctor_steps.json"
    $stepsJson = $script:RepoDoctorSteps | ConvertTo-Json -Depth 6
    Set-Content -LiteralPath $stepsPath -Value $stepsJson -Encoding utf8
  }

  Write-Host ("REPO_DOCTOR_SUMMARY|status=" + $Status + "|failed_step=" + $FailedStep + "|next=" + $Next + "|summary=" + $summaryPath)
}

function Fail-RepoDoctor {
  param(
    [string]$Reason,
    [string]$FailedStep,
    [string]$Next
  )
  Write-RepoDoctorSummary -Status "FAIL" -FailedStep $FailedStep -Next $Next
  Write-Host "REPO_DOCTOR_END"
  exit 1
}

function Invoke-RepoDoctorStep {
  param(
    [string]$Name,
    [string]$Command,
    [string[]]$Arguments
  )
  $markerPrefix = "REPO_DOCTOR_RUN_" + $Name.ToUpperInvariant()
  $runResult = Invoke-PsRunner -Command $Command -Arguments $Arguments -RepoRoot $script:RepoDoctorRepoRoot -ArtifactsDir $script:RepoDoctorArtifactsDir -MarkerPrefix $markerPrefix
  $status = if ($runResult.ExitCode -eq 0) { "PASS" } else { "FAIL" }
  $step = [ordered]@{
    name = $Name
    status = $status
    exit_code = $runResult.ExitCode
    command_line = $runResult.CommandLine
    summary_path = $runResult.SummaryPath
    stdout_path = $runResult.StdoutPath
    stderr_path = $runResult.StderrPath
  }
  $script:RepoDoctorSteps.Add($step)
  Write-Host ("REPO_DOCTOR_STEP|name=" + $Name + "|status=" + $status + "|exit_code=" + $runResult.ExitCode + "|summary=" + $runResult.SummaryPath + "|stdout=" + $runResult.StdoutPath + "|stderr=" + $runResult.StderrPath)
  return $step
}

function Resolve-RepoDoctorPython {
  param(
    [string]$RepoRoot,
    [string]$Override
  )
  if (-not [string]::IsNullOrWhiteSpace($Override)) {
    if (-not (Test-Path -LiteralPath $Override)) {
      return $null
    }
    return [IO.Path]::GetFullPath($Override)
  }
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

$script:RepoDoctorTimestamp = Get-UtcTimestamp
$script:RepoDoctorSteps = New-Object System.Collections.Generic.List[object]
$cwd = (Get-Location).Path
$repoRootValue = if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $cwd } else { $RepoRoot }

$repoRootFull = ""
if (-not [string]::IsNullOrWhiteSpace($repoRootValue) -and (Test-Path -LiteralPath $repoRootValue)) {
  $repoRootFull = [IO.Path]::GetFullPath($repoRootValue)
}
$script:RepoDoctorRepoRoot = if ([string]::IsNullOrWhiteSpace($repoRootFull)) { $repoRootValue } else { $repoRootFull }

$artifactsValue = if ([string]::IsNullOrWhiteSpace($ArtifactsDir)) { "artifacts" } else { $ArtifactsDir }
$artifactsRoot = if ([IO.Path]::IsPathRooted($artifactsValue)) { $artifactsValue } else { Join-Path $script:RepoDoctorRepoRoot $artifactsValue }
$script:RepoDoctorArtifactsDir = [IO.Path]::GetFullPath($artifactsRoot)

if (-not (Test-Path -LiteralPath $script:RepoDoctorArtifactsDir)) {
  New-Item -Force -ItemType Directory -Path $script:RepoDoctorArtifactsDir | Out-Null
}

Write-Host ("REPO_DOCTOR_START|ts_utc=" + $script:RepoDoctorTimestamp + "|cwd=" + $cwd + "|repo_root=" + $script:RepoDoctorRepoRoot + "|artifacts_dir=" + $script:RepoDoctorArtifactsDir)

if ([string]::IsNullOrWhiteSpace($repoRootFull)) {
  Fail-RepoDoctor -Reason "repo_root_missing" -FailedStep "preflight_repo_root" -Next "set -RepoRoot <path>"
}

if (-not (Test-Path -LiteralPath (Join-Path $repoRootFull ".git"))) {
  Fail-RepoDoctor -Reason "missing_git_dir" -FailedStep "preflight_git_root" -Next "set -RepoRoot <path with .git>"
}

$gitCmd = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $gitCmd -or -not $gitCmd.Source) {
  Fail-RepoDoctor -Reason "git_not_found" -FailedStep "preflight_git" -Next "install_git_and_retry"
}

$gitStatus = Invoke-PsRunner -Command $gitCmd.Source -Arguments @("status", "--porcelain") -RepoRoot $repoRootFull -ArtifactsDir $script:RepoDoctorArtifactsDir -MarkerPrefix "REPO_DOCTOR_GIT_STATUS"
if ($gitStatus.ExitCode -ne 0) {
  Fail-RepoDoctor -Reason "git_status_failed" -FailedStep "preflight_git_status" -Next "git status --porcelain"
}
$gitStdout = if (Test-Path -LiteralPath $gitStatus.StdoutPath) { Get-Content -Raw -LiteralPath $gitStatus.StdoutPath } else { "" }
$gitStderr = if (Test-Path -LiteralPath $gitStatus.StderrPath) { Get-Content -Raw -LiteralPath $gitStatus.StderrPath } else { "" }
$gitCombined = [string]::Concat([string]$gitStdout, [string]$gitStderr)
$gitCombined = $gitCombined.Trim()
if (-not [string]::IsNullOrWhiteSpace($gitCombined)) {
  Fail-RepoDoctor -Reason "dirty_worktree" -FailedStep "preflight_clean_worktree" -Next "git status --porcelain"
}

$writeDocsValue = if ($WriteDocs) { [string]$WriteDocs } else { "NO" }
$writeDocsValue = [string]::Concat($writeDocsValue, "").Trim().ToUpperInvariant()
if (-not (@("YES", "NO") -contains $writeDocsValue)) {
  Fail-RepoDoctor -Reason "invalid_write_docs" -FailedStep "preflight_write_docs" -Next "set -WriteDocs YES|NO"
}

$pythonResolved = Resolve-RepoDoctorPython -RepoRoot $repoRootFull -Override $PythonExe
if (-not $pythonResolved) {
  Fail-RepoDoctor -Reason "python_not_found" -FailedStep "preflight_python" -Next "install_python_and_retry"
}
Write-Host ("REPO_DOCTOR_CONFIG|write_docs=" + $writeDocsValue + "|python=" + $pythonResolved + "|repo_root=" + $script:RepoDoctorRepoRoot + "|artifacts_dir=" + $script:RepoDoctorArtifactsDir)

$inventoryArgs = @("-m", "tools.inventory_repo", "--artifacts-dir", $script:RepoDoctorArtifactsDir)
if ($writeDocsValue -eq "YES") {
  $inventoryArgs += "--write-docs"
}
$inventoryStep = Invoke-RepoDoctorStep -Name "inventory_repo" -Command $pythonResolved -Arguments $inventoryArgs
if ($inventoryStep.status -ne "PASS") {
  Fail-RepoDoctor -Reason "inventory_failed" -FailedStep "inventory_repo" -Next ("inspect " + $inventoryStep.summary_path)
}

$prReadyStep = Invoke-RepoDoctorStep -Name "verify_pr_ready" -Command $pythonResolved -Arguments @("-m", "tools.verify_pr_ready", "--artifacts-dir", $script:RepoDoctorArtifactsDir)
if ($prReadyStep.status -ne "PASS") {
  Fail-RepoDoctor -Reason "pr_ready_failed" -FailedStep "verify_pr_ready" -Next ("inspect " + $prReadyStep.summary_path)
}

$postStatus = Invoke-PsRunner -Command $gitCmd.Source -Arguments @("status", "--porcelain") -RepoRoot $repoRootFull -ArtifactsDir $script:RepoDoctorArtifactsDir -MarkerPrefix "REPO_DOCTOR_GIT_STATUS_POST"
if ($postStatus.ExitCode -ne 0) {
  Write-Host "REPO_DOCTOR_CLEAN_POST|status=FAIL|reason=git_status_failed|next=git status --porcelain"
  Fail-RepoDoctor -Reason "git_status_failed" -FailedStep "postflight_git_status" -Next "git status --porcelain"
}
$postStdout = if (Test-Path -LiteralPath $postStatus.StdoutPath) { Get-Content -Raw -LiteralPath $postStatus.StdoutPath } else { "" }
$postStderr = if (Test-Path -LiteralPath $postStatus.StderrPath) { Get-Content -Raw -LiteralPath $postStatus.StderrPath } else { "" }
$postCombined = [string]::Concat([string]$postStdout, [string]$postStderr)
$postCombined = $postCombined.Trim()
if (-not [string]::IsNullOrWhiteSpace($postCombined)) {
  if ($writeDocsValue -eq "YES") {
    Write-Host "REPO_DOCTOR_CLEAN_POST|status=FAIL|reason=dirty_worktree|next=git diff --name-only; git diff"
    Fail-RepoDoctor -Reason "worktree_dirty_after_write_docs" -FailedStep "postflight_clean_worktree" -Next "git diff --name-only; git diff"
  }
  Write-Host "REPO_DOCTOR_CLEAN_POST|status=FAIL|reason=dirty_worktree|next=git status --porcelain"
  Fail-RepoDoctor -Reason "dirty_worktree" -FailedStep "postflight_clean_worktree" -Next "git status --porcelain"
}
Write-Host "REPO_DOCTOR_CLEAN_POST|status=PASS|reason=ok"

Write-RepoDoctorSummary -Status "PASS" -FailedStep "none" -Next "none"
Write-Host "REPO_DOCTOR_END"
exit 0
