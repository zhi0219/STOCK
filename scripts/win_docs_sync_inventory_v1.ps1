param(
  [string]$RepoRoot = "",
  [string]$ArtifactsDir = "",
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

function Get-UtcStamp {
  return (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}

function Resolve-RepoRoot {
  param([string]$RepoRoot)
  if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    return [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
  }
  return [IO.Path]::GetFullPath($RepoRoot)
}

function Resolve-ArtifactsDir {
  param(
    [string]$RepoRoot,
    [string]$ArtifactsDir
  )
  if ([string]::IsNullOrWhiteSpace($ArtifactsDir)) {
    return [IO.Path]::GetFullPath((Join-Path $RepoRoot "artifacts"))
  }
  if ([IO.Path]::IsPathRooted($ArtifactsDir)) {
    return [IO.Path]::GetFullPath($ArtifactsDir)
  }
  return [IO.Path]::GetFullPath((Join-Path $RepoRoot $ArtifactsDir))
}

function Write-Marker {
  param([string]$Line)
  if ($null -eq $Line) { $Line = "" }
  Write-Output $Line
}

function Get-StatusPaths {
  param([string[]]$Lines)
  $paths = New-Object System.Collections.Generic.List[string]
  foreach ($line in $Lines) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    if ($line.Length -lt 4) { continue }
    $path = $line.Substring(3).Trim()
    if ($path -match " -> ") {
      $path = $path.Split(" -> ", 2)[1].Trim()
    }
    $path = $path.Replace("\\", "/")
    if (-not [string]::IsNullOrWhiteSpace($path)) {
      $paths.Add($path)
    }
  }
  return $paths
}

$repoRoot = Resolve-RepoRoot -RepoRoot $RepoRoot
$artifactsDir = Resolve-ArtifactsDir -RepoRoot $repoRoot -ArtifactsDir $ArtifactsDir
$stamp = Get-UtcStamp
$branchName = "docs-sync-inventory-$stamp"

Write-Marker "DOCS_SYNC_START"
Write-Marker "DOCS_SYNC_PLAN|repo_root=$repoRoot|artifacts_dir=$artifactsDir|branch=$branchName|apply=$($Apply.IsPresent)"

if (-not $Apply.IsPresent) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=DRY_RUN|next=Use -Apply to execute"
  Write-Marker "EXIT|code=0"
  exit 0
}

$statusOutput = (& git -C $repoRoot status --porcelain)
if ($LASTEXITCODE -ne 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=git_status_failed"
  Write-Marker "EXIT|code=1"
  exit 1
}
if ($statusOutput.Count -gt 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=dirty_worktree"
  Write-Marker "EXIT|code=1"
  exit 1
}

Write-Marker "DOCS_SYNC_APPLY|step=branch_create"
& git -C $repoRoot checkout -b $branchName
if ($LASTEXITCODE -ne 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=branch_create_failed"
  Write-Marker "EXIT|code=1"
  exit 1
}

Write-Marker "DOCS_SYNC_APPLY|step=generate_docs"
& python -m tools.inventory_repo --artifacts-dir $artifactsDir --write-docs
if ($LASTEXITCODE -ne 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=inventory_repo_failed"
  Write-Marker "EXIT|code=1"
  exit 1
}

$statusOutput = (& git -C $repoRoot status --porcelain)
if ($LASTEXITCODE -ne 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=git_status_failed"
  Write-Marker "EXIT|code=1"
  exit 1
}
$statusPaths = Get-StatusPaths -Lines $statusOutput
if ($statusPaths.Count -eq 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=PASS|detail=no_changes"
  Write-Marker "EXIT|code=0"
  exit 0
}
$disallowed = $statusPaths | Where-Object { $_ -ne "docs/inventory.md" }
if ($disallowed.Count -gt 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=unexpected_changes"
  Write-Marker "EXIT|code=1"
  exit 1
}

Write-Marker "DOCS_SYNC_APPLY|step=git_add"
& git -C $repoRoot add -- docs/inventory.md
if ($LASTEXITCODE -ne 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=git_add_failed"
  Write-Marker "EXIT|code=1"
  exit 1
}

$statusOutput = (& git -C $repoRoot status --porcelain)
if ($LASTEXITCODE -ne 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=git_status_failed"
  Write-Marker "EXIT|code=1"
  exit 1
}
$statusPaths = Get-StatusPaths -Lines $statusOutput
$disallowed = $statusPaths | Where-Object { $_ -ne "docs/inventory.md" }
if ($disallowed.Count -gt 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=unexpected_changes"
  Write-Marker "EXIT|code=1"
  exit 1
}

Write-Marker "DOCS_SYNC_APPLY|step=git_commit"
& git -C $repoRoot commit -m "docs: sync inventory"
if ($LASTEXITCODE -ne 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=git_commit_failed"
  Write-Marker "EXIT|code=1"
  exit 1
}

Write-Marker "DOCS_SYNC_APPLY|step=verify_pr_ready"
& python -m tools.verify_pr_ready --artifacts-dir $artifactsDir
if ($LASTEXITCODE -ne 0) {
  Write-Marker "DOCS_SYNC_SUMMARY|status=FAIL|reason=verify_pr_ready_failed"
  Write-Marker "EXIT|code=1"
  exit 1
}

Write-Marker "DOCS_SYNC_SUMMARY|status=PASS|branch=$branchName"
Write-Marker "EXIT|code=0"
exit 0
