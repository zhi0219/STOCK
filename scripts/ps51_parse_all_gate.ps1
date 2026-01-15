param(
  [string]$RepoRoot = "",
  [string]$ArtifactsDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-RepoRoot {
  param([string]$Root)
  if (-not [string]::IsNullOrWhiteSpace($Root)) {
    return [IO.Path]::GetFullPath($Root)
  }
  return [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}

function Resolve-ArtifactsDir {
  param(
    [string]$Root,
    [string]$Requested
  )
  $base = Join-Path $Root "artifacts"
  if ([string]::IsNullOrWhiteSpace($Requested)) {
    return [IO.Path]::GetFullPath($base)
  }
  if ([IO.Path]::IsPathRooted($Requested)) {
    return [IO.Path]::GetFullPath($Requested)
  }
  return [IO.Path]::GetFullPath((Join-Path $Root $Requested))
}

function Write-ArtifactText {
  param(
    [string]$Path,
    [string]$Content
  )
  $dir = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  Set-Content -LiteralPath $Path -Value $Content -Encoding utf8
}

function Write-ArtifactJson {
  param(
    [string]$Path,
    [hashtable]$Payload
  )
  $json = $Payload | ConvertTo-Json -Depth 8
  Write-ArtifactText -Path $Path -Content $json
}

function Get-ParseFiles {
  param([string]$Root)
  $patterns = @("*.ps1", "*.psm1")
  return @(Get-ChildItem -Path $Root -Recurse -File -Include $patterns)
}

$repoRoot = Resolve-RepoRoot -Root $RepoRoot
$artifactsDir = Resolve-ArtifactsDir -Root $repoRoot -Requested $ArtifactsDir
$failuresPath = Join-Path $artifactsDir "ps51_parse_failures.txt"
$summaryPath = Join-Path $artifactsDir "ps51_parse_summary.json"
$exceptionPath = Join-Path $artifactsDir "ps51_parse_exception.txt"

$status = "PASS"
$totalFiles = 0
$errorCount = 0
$failureLines = New-Object System.Collections.Generic.List[string]

try {
  $files = @(Get-ParseFiles -Root $repoRoot)
  $totalFiles = $files.Count
  foreach ($file in @($files)) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    $errorList = @($errors)
    if ($errorList.Count -gt 0) {
      foreach ($err in $errorList) {
        $failureLines.Add(("{0}|{1}|{2}" -f $file.FullName, $err.ErrorId, $err.Message))
        $errorCount += 1
      }
    }
  }
  if ($errorCount -gt 0) {
    $status = "FAIL"
  }
} catch {
  $status = "FAIL"
  Write-ArtifactText -Path $exceptionPath -Content $_.Exception.ToString()
}

if ($failureLines.Count -gt 0) {
  Write-ArtifactText -Path $failuresPath -Content ($failureLines -join "`n")
} else {
  Write-ArtifactText -Path $failuresPath -Content ""
}

$summary = @{
  ts_utc = [datetime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
  status = $status
  total_files = $totalFiles
  parse_errors = $errorCount
  repo_root = $repoRoot
}
Write-ArtifactJson -Path $summaryPath -Payload $summary

if ($status -ne "PASS") {
  Write-Output ("PS51_PARSE|FAIL|errors={0}" -f $errorCount)
  exit 1
}

Write-Output "PS51_PARSE|PASS"
exit 0
