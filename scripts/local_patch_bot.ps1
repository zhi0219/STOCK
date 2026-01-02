param(
  [string]$Model = "qwen2.5-coder:7b-instruct",
  [string]$EvidencePath = "work\evidence.txt",
  [string]$TemplatePath = "prompts\patch_bot_v1.txt",
  [string]$OutDir = "artifacts"
)

$ErrorActionPreference = "Stop"

function Fail($msg) {
  Write-Host "PATCH_BOT_SUMMARY|status=FAIL|reason=$msg"
  exit 1
}

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Fail "ollama_not_found" }
if (-not (Test-Path $TemplatePath)) { Fail "missing_template:$TemplatePath" }
if (-not (Test-Path $EvidencePath)) { Fail "missing_evidence:$EvidencePath" }

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path $OutDir ("patch_bot_" + $ts + ".txt")
$metaFile = Join-Path $OutDir ("patch_bot_" + $ts + "_meta.json")

$template = Get-Content $TemplatePath -Raw
$evidence = Get-Content $EvidencePath -Raw
$hash = (Get-FileHash $EvidencePath -Algorithm SHA256).Hash

$prompt = @"
$template

EVIDENCE (sha256=$hash):
$evidence
"@

Write-Host "PATCH_BOT_START|model=$Model|evidence=$EvidencePath|sha256=$hash|out=$outFile"

$uri = "http://localhost:11434/api/generate"
$body = @{
  model  = $Model
  prompt = $prompt
  stream = $false
} | ConvertTo-Json -Depth 6

try {
  $resp = Invoke-RestMethod -Method Post -Uri $uri -ContentType "application/json" -Body $body -TimeoutSec 600
} catch {
  Fail ("api_call_failed:" + $_.Exception.Message)
}

if (-not $resp -or -not $resp.response) { Fail "missing_response" }

$resp.response | Set-Content -Encoding UTF8 -NoNewline $outFile
($resp | ConvertTo-Json -Depth 10) | Set-Content -Encoding UTF8 -NoNewline $metaFile

Write-Host "PATCH_BOT_END|out=$outFile|meta=$metaFile"
Write-Host "PATCH_BOT_SUMMARY|status=PASS|out=$outFile|meta=$metaFile"