param(
  [string]$RepoRoot = "",
  [string]$ArtifactsDir = "artifacts",
  [string]$SafePullScript = "scripts/safe_pull_v1.ps1"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-UtcTimestamp {
  return [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Get-RepoRelativePath {
  param(
    [string]$Root,
    [string]$FullPath
  )
  $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd("\", "/")
  $full = [IO.Path]::GetFullPath($FullPath)
  if ($full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    $relative = $full.Substring($rootFull.Length).TrimStart("\", "/")
    if ([string]::IsNullOrWhiteSpace($relative)) { return "." }
    return $relative
  }
  return $full
}

function Resolve-FullPath {
  param([string]$Root, [string]$PathValue)
  if ([IO.Path]::IsPathRooted($PathValue)) {
    return [IO.Path]::GetFullPath($PathValue)
  }
  return [IO.Path]::GetFullPath((Join-Path $Root $PathValue))
}

function Assert-WithinArtifacts {
  param([string]$ArtifactsRoot, [string]$Candidate)
  $root = [IO.Path]::GetFullPath($ArtifactsRoot).TrimEnd("\", "/")
  $full = [IO.Path]::GetFullPath($Candidate)
  if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "artifact_outside_effective_dir:$full"
  }
}

function Invoke-SafePull {
  param(
    [string]$ShellExe,
    [string]$RepoRoot,
    [string]$ArtifactsRoot,
    [string]$SafePullScript
  )
  $safePullPath = Resolve-FullPath -Root $RepoRoot -PathValue $SafePullScript
  $args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $safePullPath,
    "-RepoRoot", $RepoRoot,
    "-ArtifactsDir", $ArtifactsRoot,
    "-DryRun", $true
  )
  & $ShellExe @args | Out-Null
  return $LASTEXITCODE
}

function Test-Contract {
  param(
    [string]$Label,
    [string]$ShellExe,
    [string]$RepoRoot,
    [string]$ArtifactsRoot,
    [string]$SafePullScript
  )

  $payload = [ordered]@{
    label = $Label
    shell = $ShellExe
    status = "PASS"
    reason = "ok"
    exit_code = 0
    checks = @()
  }

  if (-not (Get-Command $ShellExe -ErrorAction SilentlyContinue)) {
    $payload.status = "FAIL"
    $payload.reason = "shell_missing"
    $payload.checks += "shell_missing"
    return $payload
  }

  if (-not (Test-Path -LiteralPath $ArtifactsRoot)) {
    New-Item -ItemType Directory -Force -Path $ArtifactsRoot | Out-Null
  }

  $exitCode = Invoke-SafePull -ShellExe $ShellExe -RepoRoot $RepoRoot -ArtifactsRoot $ArtifactsRoot -SafePullScript $SafePullScript
  $payload.exit_code = $exitCode

  $summaryPath = Join-Path $ArtifactsRoot "safe_pull_summary.json"
  $markersPath = Join-Path $ArtifactsRoot "safe_pull_markers.txt"

  foreach ($required in @($summaryPath, $markersPath)) {
    if (-not (Test-Path -LiteralPath $required)) {
      $payload.status = "FAIL"
      $payload.reason = "missing_artifact"
      $payload.checks += "missing:$required"
      return $payload
    }
  }

  $summaryRaw = Get-Content -Raw -LiteralPath $summaryPath
  $summary = $summaryRaw | ConvertFrom-Json -ErrorAction Stop

  $expectedAbs = [IO.Path]::GetFullPath($ArtifactsRoot)
  if ($summary.artifacts_dir_abs -ne $expectedAbs) {
    $payload.status = "FAIL"
    $payload.reason = "artifacts_dir_abs_mismatch"
    $payload.checks += "summary_abs_mismatch"
    return $payload
  }

  $expectedRel = Get-RepoRelativePath -Root $RepoRoot -FullPath $expectedAbs
  if ($summary.artifacts_dir -ne $expectedRel) {
    $payload.status = "FAIL"
    $payload.reason = "artifacts_dir_rel_mismatch"
    $payload.checks += "summary_rel_mismatch"
    return $payload
  }

  $runStart = (Get-Content -LiteralPath $markersPath) | Where-Object { $_ -like "SAFE_PULL_RUN_START*" } | Select-Object -First 1
  if (-not $runStart) {
    $payload.status = "FAIL"
    $payload.reason = "missing_run_start"
    $payload.checks += "missing_run_start"
    return $payload
  }

  $artifactToken = ($runStart -split "\|") | Where-Object { $_ -like "artifacts_dir=*" } | Select-Object -First 1
  $markerArtifacts = $artifactToken.Replace("artifacts_dir=", "")
  if ($markerArtifacts -ne $expectedRel) {
    $payload.status = "FAIL"
    $payload.reason = "marker_artifacts_dir_mismatch"
    $payload.checks += "marker_artifacts_dir_mismatch"
    return $payload
  }

  if ($summary.reason -like "dirty_worktree_dry_run*") {
    if ($summary.reason -notmatch "^dirty_worktree_dry_run:tracked=\d+:untracked=\d+$") {
      $payload.status = "FAIL"
      $payload.reason = "dirty_worktree_reason_format"
      $payload.checks += "dirty_worktree_reason_format"
      return $payload
    }
  }

  if ($summary.next -and $summary.next -ne "none") {
    $nextPath = $summary.next
    $nextFull = if ([IO.Path]::IsPathRooted($nextPath)) { $nextPath } else { Join-Path $RepoRoot $nextPath }
    if (-not (Test-Path -LiteralPath $nextFull)) {
      $payload.status = "FAIL"
      $payload.reason = "missing_next_evidence"
      $payload.checks += "missing_next_evidence"
      return $payload
    }
    try {
      Assert-WithinArtifacts -ArtifactsRoot $expectedAbs -Candidate $nextFull
    } catch {
      $payload.status = "FAIL"
      $payload.reason = "next_outside_artifacts"
      $payload.checks += "next_outside_artifacts"
      return $payload
    }
  }

  return $payload
}

$repoRoot = if ([string]::IsNullOrWhiteSpace($RepoRoot)) { (Get-Location).Path } else { $RepoRoot }
$repoRoot = [IO.Path]::GetFullPath($repoRoot)
$artifactsRoot = Resolve-FullPath -Root $repoRoot -PathValue $ArtifactsDir
if (-not (Test-Path -LiteralPath $artifactsRoot)) {
  New-Item -ItemType Directory -Force -Path $artifactsRoot | Out-Null
}

$ps51Dir = Join-Path $artifactsRoot "_contract_ps51"
$ps7Dir = Join-Path $artifactsRoot "_contract_ps7"

$results = @()
$results += Test-Contract -Label "artifacts_dir_contract_ps51" -ShellExe "powershell" -RepoRoot $repoRoot -ArtifactsRoot $ps51Dir -SafePullScript $SafePullScript
$results += Test-Contract -Label "artifacts_dir_contract_ps7" -ShellExe "pwsh" -RepoRoot $repoRoot -ArtifactsRoot $ps7Dir -SafePullScript $SafePullScript

$status = "PASS"
$failure = $results | Where-Object { $_.status -ne "PASS" } | Select-Object -First 1
if ($failure) { $status = "FAIL" }

$summary = [ordered]@{
  status = $status
  results = $results
  ts_utc = Get-UtcTimestamp
}

$summaryPath = Join-Path $artifactsRoot "safe_pull_artifacts_dir_contract.json"
$summaryJson = $summary | ConvertTo-Json -Depth 8
Set-Content -LiteralPath $summaryPath -Value $summaryJson -Encoding utf8

Write-Output "SAFE_PULL_ARTIFACTS_DIR_CONTRACT_START"
foreach ($result in $results) {
  Write-Output ("SAFE_PULL_ARTIFACTS_DIR_CONTRACT_RESULT|name=" + $result.label + "|status=" + $result.status + "|reason=" + $result.reason)
}
Write-Output ("SAFE_PULL_ARTIFACTS_DIR_CONTRACT_SUMMARY|status=" + $status + "|artifacts_dir=" + $artifactsRoot)
Write-Output "SAFE_PULL_ARTIFACTS_DIR_CONTRACT_END"

if ($status -ne "PASS") { exit 1 }
exit 0
