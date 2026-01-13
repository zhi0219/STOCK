param(
  [string]$ArtifactsDir = "artifacts",
  [string]$ScriptPath = "scripts/safe_pull_v1.ps1"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Result {
  param(
    [string]$Path,
    [hashtable]$Payload
  )
  $dir = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  $json = $Payload | ConvertTo-Json -Depth 6
  Set-Content -LiteralPath $Path -Value $json -Encoding utf8
}

$repoRoot = [IO.Path]::GetFullPath((Get-Location).Path)
$artifactsRoot = if ([IO.Path]::IsPathRooted($ArtifactsDir)) { $ArtifactsDir } else { Join-Path $repoRoot $ArtifactsDir }
$artifactsRoot = [IO.Path]::GetFullPath($artifactsRoot)
$targetScript = if ([IO.Path]::IsPathRooted($ScriptPath)) { $ScriptPath } else { Join-Path $repoRoot $ScriptPath }
$targetScript = [IO.Path]::GetFullPath($targetScript)

$errors = $null
$tokens = $null
[System.Management.Automation.Language.Parser]::ParseFile($targetScript, [ref]$tokens, [ref]$errors) | Out-Null
$errorList = @()
if ($errors) {
  $errorList = @($errors | ForEach-Object { $_.ToString() })
}
$status = if ($errorList.Count -gt 0) { "FAIL" } else { "PASS" }

$payload = [ordered]@{
  status = $status
  script = $targetScript
  errors = $errorList
  ts_utc = (Get-Date -AsUTC).ToString("yyyy-MM-ddTHH:mm:ssZ")
}

$resultPath = Join-Path $artifactsRoot "ps_parse_safe_pull_result.json"
Write-Result -Path $resultPath -Payload $payload

Write-Output "PS_PARSE_GUARD_START"
Write-Output ("PS_PARSE_GUARD_SUMMARY|status=" + $status + "|errors=" + $errorList.Count + "|script=" + $targetScript + "|artifacts_dir=" + $artifactsRoot)
Write-Output "PS_PARSE_GUARD_END"

if ($status -ne "PASS") { exit 1 }
exit 0
