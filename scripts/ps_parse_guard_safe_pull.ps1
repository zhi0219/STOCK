param(
  [string]$ArtifactsDir = "artifacts",
  [string]$ScriptPath = "scripts/safe_pull_v1.ps1"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-UtcTimestamp {
  return [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
}

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

$scanPath = Join-Path $artifactsRoot "no_asutc_scan.txt"
$scanLines = Get-Content -LiteralPath $targetScript -ErrorAction Stop
$scanMatches = @()
for ($idx = 0; $idx -lt $scanLines.Count; $idx++) {
  if ($scanLines[$idx] -match "-AsUTC") {
    $scanMatches += ("line=" + ($idx + 1) + "|text=" + $scanLines[$idx])
  }
}
if ($scanMatches.Count -gt 0) {
  Write-Result -Path $scanPath -Payload @{ status = "FAIL"; matches = $scanMatches }
  Write-Output "PS_PARSE_GUARD_START"
  Write-Output ("PS_PARSE_GUARD_SUMMARY|status=FAIL|reason=contains_AsUTC|script=" + $targetScript + "|artifacts_dir=" + $artifactsRoot)
  Write-Output "PS_PARSE_GUARD_END"
  exit 1
}
Write-Result -Path $scanPath -Payload @{ status = "PASS"; matches = @() }

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
  ts_utc = Get-UtcTimestamp
}

$resultPath = Join-Path $artifactsRoot "ps_parse_safe_pull_result.json"
Write-Result -Path $resultPath -Payload $payload

Write-Output "PS_PARSE_GUARD_START"
Write-Output ("PS_PARSE_GUARD_SUMMARY|status=" + $status + "|errors=" + $errorList.Count + "|script=" + $targetScript + "|artifacts_dir=" + $artifactsRoot)
Write-Output "PS_PARSE_GUARD_END"

if ($status -ne "PASS") { exit 1 }
exit 0
