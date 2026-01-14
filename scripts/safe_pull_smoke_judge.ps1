param(
  [string]$RepoRoot = "",
  [string]$ArtifactsDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-JsonSafe {
  param(
    [string]$Path
  )
  try {
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Get-ArtifactPaths {
  param(
    [string]$ArtifactsDir
  )
  $summaryPath = Join-Path $ArtifactsDir "safe_pull_summary.json"
  $exceptionPath = Join-Path $ArtifactsDir "safe_pull_exception.json"
  $markersPath = Join-Path $ArtifactsDir "safe_pull_markers.txt"
  return [ordered]@{
    summary = $summaryPath
    exception = $exceptionPath
    markers = $markersPath
  }
}

function List-Directory {
  param(
    [string]$Path
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    return @()
  }
  return @(
    Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty Name
  )
}

function Find-Artifacts {
  param(
    [string]$RootDir,
    [string]$FileName
  )
  if (-not (Test-Path -LiteralPath $RootDir)) {
    return @()
  }
  return @(
    Get-ChildItem -Path $RootDir -Filter $FileName -Recurse -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty FullName
  )
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  $RepoRoot = (Get-Location).Path
}

if ([string]::IsNullOrWhiteSpace($ArtifactsDir)) {
  throw "missing_artifacts_dir"
}

$artifactsFull = if ([IO.Path]::IsPathRooted($ArtifactsDir)) { $ArtifactsDir } else { Join-Path $RepoRoot $ArtifactsDir }
$artifactsFull = [IO.Path]::GetFullPath($artifactsFull)
$paths = Get-ArtifactPaths -ArtifactsDir $artifactsFull
$summaryPath = $paths.summary
$exceptionPath = $paths.exception
$markersPath = $paths.markers

Write-Host ("SAFE_PULL_SMOKE_DIR|artifacts_dir=" + $artifactsFull + "|summary_path=" + $summaryPath)

$summaryExists = Test-Path -LiteralPath $summaryPath
$markersExists = Test-Path -LiteralPath $markersPath
$exceptionExists = Test-Path -LiteralPath $exceptionPath

if (-not $summaryExists) {
  $artifactRoot = Join-Path $RepoRoot "artifacts"
  $foundSummaries = Find-Artifacts -RootDir $artifactRoot -FileName "safe_pull_summary.json"
  $foundSummaryText = if ($foundSummaries.Count -gt 0) { ($foundSummaries -join ";") } else { "none" }
  $foundExceptions = Find-Artifacts -RootDir $artifactRoot -FileName "safe_pull_exception.json"
  if ($foundExceptions.Count -gt 0) {
    foreach ($path in $foundExceptions) {
      $payload = Get-JsonSafe -Path $path
      if ($payload) {
        Write-Host ("SAFE_PULL_SMOKE_EXCEPTION|path=" + $path + "|type=" + $payload.type + "|message=" + $payload.message + "|phase=" + $payload.phase)
      } else {
        Write-Host ("SAFE_PULL_SMOKE_EXCEPTION|path=" + $path + "|type=invalid_json")
      }
    }
  }
  $dirListing = List-Directory -Path $artifactsFull
  $dirText = if ($dirListing.Count -gt 0) { ($dirListing -join ";") } else { "empty" }
  Write-Host ("SAFE_PULL_SMOKE_DIR_LIST|artifacts_dir=" + $artifactsFull + "|entries=" + $dirText)
  throw ("safe_pull_summary_missing|artifacts_dir=" + $artifactsFull + "|found=" + $foundSummaryText)
}

if (-not $markersExists) {
  throw ("safe_pull_markers_missing|artifacts_dir=" + $artifactsFull)
}

if ($exceptionExists) {
  $payload = Get-JsonSafe -Path $exceptionPath
  if ($payload) {
    Write-Host ("SAFE_PULL_SMOKE_EXCEPTION|path=" + $exceptionPath + "|type=" + $payload.type + "|message=" + $payload.message + "|phase=" + $payload.phase)
  }
  throw ("safe_pull_exception_present|artifacts_dir=" + $artifactsFull)
}

$summary = Get-JsonSafe -Path $summaryPath
if (-not $summary) {
  throw ("safe_pull_summary_invalid|artifacts_dir=" + $artifactsFull)
}

$summaryLine = "SAFE_PULL_SMOKE_JUDGE|status=$($summary.status)|reason=$($summary.reason)|phase=$($summary.phase)|next=$($summary.next)|mode=$($summary.mode)|artifacts_dir=$($summary.artifacts_dir)"
Write-Host $summaryLine

if ($summary.reason -eq "internal_exception") {
  throw ("safe_pull_internal_exception|artifacts_dir=" + $artifactsFull)
}

if ($summary.status -ne "PASS") {
  throw ("safe_pull_summary_not_pass|status=" + $summary.status + "|reason=" + $summary.reason + "|artifacts_dir=" + $artifactsFull)
}
