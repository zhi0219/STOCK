param(
  [string]$RepoRoot = "",
  [string]$ArtifactsDir = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "powershell_runner.ps1")

function Get-UtcTimestamp {
  return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
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

function Fail-InventoryRefresh {
  param(
    [string]$Reason,
    [string]$Next
  )
  if ([string]::IsNullOrWhiteSpace($Reason)) { $Reason = "unknown" }
  if ([string]::IsNullOrWhiteSpace($Next)) { $Next = "none" }
  Write-Host ("INVENTORY_REFRESH_SUMMARY|status=FAIL|reason=" + $Reason + "|next=" + $Next)
  Write-Host "INVENTORY_REFRESH_END"
  exit 1
}

$ts = Get-UtcTimestamp
$cwd = (Get-Location).Path
$repoRootValue = if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $cwd } else { $RepoRoot }
$repoRootFull = ""
if (-not [string]::IsNullOrWhiteSpace($repoRootValue) -and (Test-Path -LiteralPath $repoRootValue)) {
  $repoRootFull = [IO.Path]::GetFullPath($repoRootValue)
}
if ([string]::IsNullOrWhiteSpace($repoRootFull)) {
  Write-Host ("INVENTORY_REFRESH_START|ts_utc=" + $ts + "|cwd=" + $cwd)
  Fail-InventoryRefresh -Reason "repo_root_missing" -Next "set -RepoRoot <path>"
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRootFull ".git"))) {
  Write-Host ("INVENTORY_REFRESH_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|repo_root=" + $repoRootFull)
  Fail-InventoryRefresh -Reason "missing_git_dir" -Next "set -RepoRoot <path with .git>"
}

$artifactsValue = if ([string]::IsNullOrWhiteSpace($ArtifactsDir)) { "artifacts" } else { $ArtifactsDir }
$artifactsRoot = if ([IO.Path]::IsPathRooted($artifactsValue)) { $artifactsValue } else { Join-Path $repoRootFull $artifactsValue }
$artifactsDir = [IO.Path]::GetFullPath($artifactsRoot)
if (-not (Test-Path -LiteralPath $artifactsDir)) {
  New-Item -Force -ItemType Directory -Path $artifactsDir | Out-Null
}

$pythonExe = Resolve-PythonExe -RepoRoot $repoRootFull
if (-not $pythonExe) {
  Write-Host ("INVENTORY_REFRESH_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|repo_root=" + $repoRootFull + "|artifacts_dir=" + $artifactsDir)
  Fail-InventoryRefresh -Reason "python_not_found" -Next "install_python_and_retry"
}

Write-Host ("INVENTORY_REFRESH_START|ts_utc=" + $ts + "|cwd=" + $cwd + "|repo_root=" + $repoRootFull + "|artifacts_dir=" + $artifactsDir + "|python=" + $pythonExe)
Write-Host "INVENTORY_REFRESH_WARNING|detail=This command will modify docs/inventory.md and may require a PR."

$inventoryStep = Invoke-PsRunner -Command $pythonExe -Arguments @("-m", "tools.inventory_repo", "--artifacts-dir", $artifactsDir, "--write-docs") -RepoRoot $repoRootFull -ArtifactsDir $artifactsDir -MarkerPrefix "INVENTORY_REFRESH_RUN"
if ($inventoryStep.ExitCode -ne 0) {
  Fail-InventoryRefresh -Reason "inventory_failed" -Next ("inspect " + $inventoryStep.SummaryPath)
}

Write-Host "INVENTORY_REFRESH_SUMMARY|status=PASS|reason=ok|next=review docs/inventory.md"
Write-Host "INVENTORY_REFRESH_END"
exit 0
