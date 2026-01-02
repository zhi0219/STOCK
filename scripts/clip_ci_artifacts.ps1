param(
  [string]$ArtifactsDir = "work\ci_artifacts\latest",
  [string]$OutEvidencePath = "work\evidence.txt",
  [string]$OutDir = "artifacts",
  [int]$MaxLinesTotal = 260
)

$ErrorActionPreference = "Stop"

function Fail($msg) {
  Write-Host "CI_CLIP_SUMMARY|status=FAIL|reason=$msg"
  exit 1
}

function Find-One($dir, $name) {
  $hit = Get-ChildItem -Path $dir -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq $name } | Select-Object -First 1
  return $hit
}

function Read-Text($path, $maxChars) {
  $t = Get-Content $path -Raw
  if ($t.Length -gt $maxChars) { return $t.Substring(0, $maxChars) + "`n...[TRUNCATED]..." }
  return $t
}

function Clip-Lines-With-Context($path, $patterns, $ctx, $maxBlocks, $maxLinesTotal) {
  $lines = Get-Content $path
  $hits = New-Object System.Collections.Generic.List[int]
  for ($i=0; $i -lt $lines.Count; $i++) {
    foreach ($p in $patterns) {
      if ($lines[$i] -match $p) { $hits.Add($i); break }
    }
  }
  $hits = $hits | Select-Object -Unique | Select-Object -First ($maxBlocks * 3)

  $blocks = New-Object System.Collections.Generic.List[string]
  $used = 0
  foreach ($h in $hits) {
    $start = [Math]::Max(0, $h - $ctx)
    $end = [Math]::Min($lines.Count - 1, $h + $ctx)
    $slice = $lines[$start..$end]
    $used += $slice.Count
    if ($used -gt $maxLinesTotal) { break }
    $blocks.Add(("---- CONTEXT lines {0}-{1} ----`n{2}" -f ($start+1), ($end+1), ($slice -join "`n")))
  }
  if ($blocks.Count -eq 0) {
    $tailN = [Math]::Min($lines.Count, [Math]::Min(120, $maxLinesTotal))
    $tail = $lines[($lines.Count-$tailN)..($lines.Count-1)]
    return ("---- TAIL last {0} lines ----`n{1}" -f $tailN, ($tail -join "`n"))
  }
  return ($blocks -join "`n`n")
}

New-Item -ItemType Directory -Force -Path $ArtifactsDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $OutEvidencePath -Parent) | Out-Null

$proof = Find-One $ArtifactsDir "proof_summary.json"
$job   = Find-One $ArtifactsDir "ci_job_summary.md"
$gates = Find-One $ArtifactsDir "gates.log"

if (-not $proof) { Fail "missing_proof_summary.json_in:$ArtifactsDir" }
if (-not $job)   { Fail "missing_ci_job_summary.md_in:$ArtifactsDir" }
if (-not $gates) { Fail "missing_gates.log_in:$ArtifactsDir" }

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outMeta = Join-Path $OutDir ("ci_clip_" + $ts + "_meta.json")

$hProof = (Get-FileHash $proof.FullName -Algorithm SHA256).Hash
$hJob   = (Get-FileHash $job.FullName   -Algorithm SHA256).Hash
$hGates = (Get-FileHash $gates.FullName -Algorithm SHA256).Hash

Write-Host "CI_CLIP_START|dir=$ArtifactsDir|proof=$($proof.FullName)|job=$($job.FullName)|gates=$($gates.FullName)"

$proofObj = $null
try { $proofObj = (Get-Content $proof.FullName -Raw | ConvertFrom-Json) } catch { $proofObj = $null }

$proofKey = @{}
if ($proofObj) {
  foreach ($k in @("status","failing_gate","failing_step","failing_check","reason","summary","highlights","next")) {
    if ($proofObj.PSObject.Properties.Name -contains $k) { $proofKey[$k] = $proofObj.$k }
  }
}

$proofText = Read-Text $proof.FullName 2400
$jobText   = Read-Text $job.FullName   2400

$gatesClip = Clip-Lines-With-Context -path $gates.FullName -patterns @("Traceback","Exception","ImportError","ModuleNotFoundError","NameError","SyntaxError","FAIL","ERROR") -ctx 25 -maxBlocks 5 -maxLinesTotal 220

$evidence = @()
$evidence += "EVIDENCE_PACK_V1_START"
$evidence += ("SOURCE|proof_summary.json|sha256={0}" -f $hProof)
if ($proofKey.Count -gt 0) {
  $evidence += "PROOF_KEYS_START"
  $evidence += ($proofKey.GetEnumerator() | Sort-Object Name | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value })
  $evidence += "PROOF_KEYS_END"
}
$evidence += $proofText
$evidence += ""
$evidence += ("SOURCE|ci_job_summary.md|sha256={0}" -f $hJob)
$evidence += $jobText
$evidence += ""
$evidence += ("SOURCE|gates.log|sha256={0}" -f $hGates)
$evidence += $gatesClip
$evidence += "EVIDENCE_PACK_V1_END"

if ($evidence.Count -gt $MaxLinesTotal) {
  $evidence = $evidence[0..($MaxLinesTotal-1)]
  $evidence += "...[CLIPPED_BY_MAX_LINES_TOTAL]..."
}

$evidence -join "`n" | Set-Content -Encoding UTF8 -NoNewline $OutEvidencePath

$meta = @{
  status = "PASS"
  artifacts_dir = $ArtifactsDir
  inputs = @(
    @{ name="proof_summary.json"; path=$proof.FullName; sha256=$hProof },
    @{ name="ci_job_summary.md"; path=$job.FullName; sha256=$hJob },
    @{ name="gates.log"; path=$gates.FullName; sha256=$hGates }
  )
  out_evidence = @{ path=$OutEvidencePath; sha256=(Get-FileHash $OutEvidencePath -Algorithm SHA256).Hash }
  max_lines_total = $MaxLinesTotal
} | ConvertTo-Json -Depth 8

$meta | Set-Content -Encoding UTF8 -NoNewline $outMeta

Write-Host "CI_CLIP_END|evidence=$OutEvidencePath|meta=$outMeta"
Write-Host "CI_CLIP_SUMMARY|status=PASS|evidence=$OutEvidencePath|meta=$outMeta"