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

function Get-ScanFiles {
  param([string]$Root)
  $patterns = @("*.ps1", "*.psm1", "*.yml")
  return @(Get-ChildItem -Path $Root -Recurse -File -Include $patterns)
}

function Get-SelectStringHits {
  param(
    [string[]]$Paths,
    [string]$Pattern,
    [switch]$SimpleMatch
  )
  $hits = @()
  foreach ($path in $Paths) {
    if ($SimpleMatch) {
      $matches = @(Select-String -LiteralPath $path -Pattern $Pattern -SimpleMatch -ErrorAction SilentlyContinue)
    } else {
      $matches = @(Select-String -LiteralPath $path -Pattern $Pattern -ErrorAction SilentlyContinue)
    }
    if ($matches) {
      $hits += @($matches)
    }
  }
  return @($hits)
}

function Format-HitLines {
  param(
    [string]$Label,
    [object[]]$Hits
  )
  $lines = New-Object System.Collections.Generic.List[string]
  foreach ($hit in @($Hits)) {
    $text = $hit.Line
    if ($null -eq $text) { $text = "" }
    $lines.Add(("{0}|{1}:{2}:{3}" -f $Label, $hit.Path, $hit.LineNumber, $text))
  }
  return $lines
}

function Find-CountRisks {
  param(
    [string]$Path,
    [string[]]$ExecutedScripts
  )
  $content = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
  if ($null -eq $content) { $content = "" }
  $lines = @($content -split "`r?`n")
  $normalized = @{}
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    $assignMatch = [regex]::Match($line, "^\s*\$(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*@\(")
    if ($assignMatch.Success) {
      $varName = $assignMatch.Groups["name"].Value
      $normalized[$varName] = $true
    }
    $listMatch = [regex]::Match($line, "^\s*\$(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*New-Object\s+System\.Collections\.Generic\.List")
    if ($listMatch.Success) {
      $varName = $listMatch.Groups["name"].Value
      $normalized[$varName] = $true
    }
  }

  $risky = New-Object System.Collections.Generic.List[object]
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    $countMatches = [regex]::Matches($line, "\$(?<name>[A-Za-z_][A-Za-z0-9_]*)\.Count")
    foreach ($match in $countMatches) {
      $varName = $match.Groups["name"].Value
      if (-not $normalized.ContainsKey($varName)) {
        $risky.Add([PSCustomObject]@{
          Path = $Path
          LineNumber = $i + 1
          Line = $line
          Variable = $varName
          Executed = ($ExecutedScripts -contains (Split-Path -Leaf $Path))
        })
      }
    }
  }
  return @($risky)
}

$repoRoot = Resolve-RepoRoot -Root $RepoRoot
$artifactsDir = Resolve-ArtifactsDir -Root $repoRoot -Requested $ArtifactsDir
$hitsPath = Join-Path $artifactsDir "refscan_hits.txt"
$summaryPath = Join-Path $artifactsDir "refscan_summary.json"
$exceptionPath = Join-Path $artifactsDir "refscan_exception.txt"

$status = "PASS"
$missingRefCount = 0
$getDateCount = 0
$riskyCount = 0
$riskyBlocking = 0
$missingTargets = @()
$hitLines = New-Object System.Collections.Generic.List[string]

$executedScripts = @(
  "safe_pull_v1.ps1",
  "ps51_ref_scan_gate.ps1",
  "ps51_parse_all_gate.ps1"
)

try {
  $files = @(Get-ScanFiles -Root $repoRoot)
  $paths = @($files | ForEach-Object { $_.FullName })

  $missingNames = @(
    "safe_pull_smoke_judge",
    "ps_parse_guard_safe_pull",
    "verify_safe_pull_contract"
  )

  foreach ($name in $missingNames) {
    $matches = @(Get-SelectStringHits -Paths $paths -Pattern $name -SimpleMatch)
    if ($matches.Count -gt 0) {
      $target = Join-Path $repoRoot (Join-Path "scripts" ($name + ".ps1"))
      if (-not (Test-Path -LiteralPath $target)) {
        $missingTargets += $name
        $missingRefCount += $matches.Count
        $hitLines.AddRange((Format-HitLines -Label ("missing_ref:" + $name) -Hits $matches))
      }
    }
  }

  $dateMatches = @(Get-SelectStringHits -Paths $paths -Pattern "Get-Date\s+-AsUTC")
  if ($dateMatches.Count -gt 0) {
    $getDateCount = $dateMatches.Count
    $hitLines.AddRange((Format-HitLines -Label "get_date_asutc" -Hits $dateMatches))
  }

  $gateFiles = @($files | Where-Object {
    $_.Name -match "gate" -or $_.Name -match "judge"
  })
  $riskyHits = New-Object System.Collections.Generic.List[object]
  foreach ($gateFile in $gateFiles) {
    $riskyHits.AddRange((Find-CountRisks -Path $gateFile.FullName -ExecutedScripts $executedScripts))
  }

  $riskyCount = $riskyHits.Count
  if ($riskyCount -gt 0) {
    foreach ($hit in $riskyHits) {
      $label = if ($hit.Executed) { "count_risk_blocking" } else { "count_risk_warn" }
      $hitLines.Add("{0}|{1}:{2}:{3}" -f $label, $hit.Path, $hit.LineNumber, $hit.Line)
      if ($hit.Executed) { $riskyBlocking += 1 }
    }
  }

  if ($missingTargets.Count -gt 0) {
    $status = "FAIL"
  }
  if ($getDateCount -gt 0) {
    $status = "FAIL"
  }
  if ($riskyBlocking -gt 0) {
    $status = "FAIL"
  }
} catch {
  $status = "FAIL"
  Write-ArtifactText -Path $exceptionPath -Content $_.Exception.ToString()
}

if ($hitLines.Count -gt 0) {
  Write-ArtifactText -Path $hitsPath -Content ($hitLines -join "`n")
} else {
  Write-ArtifactText -Path $hitsPath -Content ""
}

$summary = @{
  ts_utc = [datetime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
  status = $status
  missing_ref_hits = $missingRefCount
  missing_ref_targets = @($missingTargets)
  get_date_asutc_hits = $getDateCount
  count_risk_hits = $riskyCount
  count_risk_blocking = $riskyBlocking
  repo_root = $repoRoot
}
Write-ArtifactJson -Path $summaryPath -Payload $summary

if ($status -ne "PASS") {
  if ($missingTargets.Count -gt 0) {
    $missingTargets | ForEach-Object {
      Write-Output ("REFSCAN|FAIL|missing={0}|hits={1}" -f $_, $missingRefCount)
    }
  }
  if ($getDateCount -gt 0) {
    Write-Output ("REFSCAN|FAIL|get_date_asutc_hits={0}" -f $getDateCount)
  }
  if ($riskyBlocking -gt 0) {
    Write-Output ("REFSCAN|FAIL|count_risk_blocking={0}" -f $riskyBlocking)
  }
  exit 1
}

Write-Output "REFSCAN|PASS"
exit 0
