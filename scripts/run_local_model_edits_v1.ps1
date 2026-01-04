param(
  [string]$RepoRoot = "C:\DONE\MONEY\STOCK",
  [string]$Model = "",
  [string]$PromptText = "",
  [string]$PromptPath = "",
  [string]$OutDir = "artifacts",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Utf8NoBomLf {
  param(
    [string]$Path,
    [string]$Text
  )
  $normalized = ($Text | Out-String)
  $normalized = $normalized -replace "`r`n", "`n"
  $normalized = $normalized -replace "`r", "`n"
  if (-not $normalized.EndsWith("`n")) {
    $normalized += "`n"
  }
  $enc = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($Path, $normalized, $enc)
}

function Write-RunLocalModelArtifacts {
  param(
    [string]$ArtifactsDir,
    [string]$Status,
    [string]$Reason,
    [string]$Detail,
    [string]$RepoRoot,
    [string]$Next,
    [string]$SummaryPath,
    [bool]$DryRun
  )
  if (-not (Test-Path -LiteralPath $ArtifactsDir)) {
    New-Item -Force -ItemType Directory $ArtifactsDir | Out-Null
  }

  $detailText = ($Detail | Out-String).Trim()
  $summaryLines = @(
    "RUN_LOCAL_MODEL_SUMMARY|status=$Status|reason=$Reason|next=$Next",
    "repo_root=$RepoRoot",
    "artifacts_dir=$ArtifactsDir",
    "dry_run=$DryRun",
    "detail=$detailText"
  )
  Write-Utf8NoBomLf -Path $SummaryPath -Text ($summaryLines -join "`n")

  $resultPath = Join-Path $ArtifactsDir "apply_edits_result.json"
  if (-not (Test-Path -LiteralPath $resultPath)) {
    $payload = @{
      version = "v1"
      status = $Status
      reason = $Reason
      detail = $detailText
      repo_root = $RepoRoot
      dry_run = $DryRun
      next = $Next
    }
    Write-Utf8NoBomLf -Path $resultPath -Text ($payload | ConvertTo-Json -Depth 6)
  }
}

function Fail {
  param(
    [string]$Reason,
    [string]$Detail,
    [string]$RepoRoot,
    [string]$ArtifactsDir,
    [string]$Next,
    [string]$SummaryPath,
    [bool]$DryRun
  )
  Write-RunLocalModelArtifacts `
    -ArtifactsDir $ArtifactsDir `
    -Status "FAIL" `
    -Reason $Reason `
    -Detail $Detail `
    -RepoRoot $RepoRoot `
    -Next $Next `
    -SummaryPath $SummaryPath `
    -DryRun $DryRun
  $detailText = ($Detail | Out-String).Trim()
  Write-Host ("RUN_LOCAL_MODEL_SUMMARY|status=FAIL|reason=" + $Reason + "|detail=" + $detailText + "|next=" + $Next)
  Write-Host "RUN_LOCAL_MODEL_END"
  exit 1
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
Set-Location -LiteralPath $RepoRoot

$OutDirAbs = if ([IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $RepoRoot $OutDir }
$OutDirAbs = [IO.Path]::GetFullPath($OutDirAbs)
New-Item -Force -ItemType Directory $OutDirAbs | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$rawOut = Join-Path $OutDirAbs ("ollama_raw_{0}.txt" -f $timestamp)
$editsJson = Join-Path $OutDirAbs ("edits_{0}.json" -f $timestamp)
$summaryPath = Join-Path $OutDirAbs ("run_local_model_summary_{0}.txt" -f $timestamp)

if ([string]::IsNullOrWhiteSpace($Model)) {
  Fail "missing_model" "model_required" $RepoRoot $OutDirAbs "pass_model" $summaryPath $DryRun.IsPresent
}

if ([string]::IsNullOrWhiteSpace($PromptText) -and [string]::IsNullOrWhiteSpace($PromptPath)) {
  Fail "missing_prompt" "prompt_required" $RepoRoot $OutDirAbs "pass_prompt_text_or_path" $summaryPath $DryRun.IsPresent
}

if (-not [string]::IsNullOrWhiteSpace($PromptText) -and -not [string]::IsNullOrWhiteSpace($PromptPath)) {
  Fail "prompt_conflict" "use_one_prompt_source" $RepoRoot $OutDirAbs "use_prompt_text_or_path" $summaryPath $DryRun.IsPresent
}

$promptAbs = ""
if (-not [string]::IsNullOrWhiteSpace($PromptPath)) {
  $promptAbs = if ([IO.Path]::IsPathRooted($PromptPath)) { $PromptPath } else { Join-Path $RepoRoot $PromptPath }
  $promptAbs = [IO.Path]::GetFullPath($promptAbs)
  if (-not (Test-Path -LiteralPath $promptAbs)) {
    Fail "prompt_path_missing" $promptAbs $RepoRoot $OutDirAbs "write_prompt_file" $summaryPath $DryRun.IsPresent
  }
} else {
  $promptAbs = Join-Path $OutDirAbs ("prompt_{0}.txt" -f $timestamp)
  Write-Utf8NoBomLf -Path $promptAbs -Text $PromptText
}

Write-Host ("RUN_LOCAL_MODEL_START|repo=" + $RepoRoot + "|model=" + $Model + "|out_dir=" + $OutDirAbs + "|dry_run=" + $DryRun.IsPresent)

$pythonCmd = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonCmd)) {
  $pythonCmd = (Get-Command python -ErrorAction SilentlyContinue).Path
}
if (-not $pythonCmd) {
  Fail "missing_python" "python_not_found" $RepoRoot $OutDirAbs "install_python_or_venv" $summaryPath $DryRun.IsPresent
}

& $pythonCmd -m tools.run_ollama --model $Model --prompt-file $promptAbs --out-file $rawOut
$ollamaExit = $LASTEXITCODE
if ($ollamaExit -ne 0) {
  Fail "ollama_failed" ("exit_code=" + $ollamaExit) $RepoRoot $OutDirAbs "inspect_ollama_output" $summaryPath $DryRun.IsPresent
}

if (-not (Test-Path -LiteralPath $rawOut)) {
  Fail "ollama_missing_output" $rawOut $RepoRoot $OutDirAbs "inspect_ollama_output" $summaryPath $DryRun.IsPresent
}

$rawText = (Get-Content -LiteralPath $rawOut -Raw | Out-String)
if ([string]::IsNullOrWhiteSpace($rawText)) {
  Fail "ollama_empty_output" "empty_stdout" $RepoRoot $OutDirAbs "regenerate_output" $summaryPath $DryRun.IsPresent
}

& $pythonCmd -m tools.extract_json_strict --raw-text $rawOut --out-json $editsJson
$extractExit = $LASTEXITCODE
if ($extractExit -ne 0) {
  Fail "extract_json_strict_failed" ("exit_code=" + $extractExit) $RepoRoot $OutDirAbs "inspect_extractor_errors" $summaryPath $DryRun.IsPresent
}

$scaffoldOut = Join-Path $OutDirAbs "scaffold_edits_payload.json"
& $pythonCmd -m tools.scaffold_edits_payload --edits-json $editsJson --artifacts-dir $OutDirAbs
$scaffoldExit = $LASTEXITCODE
if ($scaffoldExit -ne 0) {
  Fail "scaffold_edits_payload_failed" ("exit_code=" + $scaffoldExit) $RepoRoot $OutDirAbs "inspect_scaffold_edits_payload" $summaryPath $DryRun.IsPresent
}

& $pythonCmd -m tools.verify_edits_payload --edits-path $scaffoldOut --artifacts-dir $OutDirAbs
$verifyExit = $LASTEXITCODE
if ($verifyExit -ne 0) {
  Fail "verify_edits_payload_failed" ("exit_code=" + $verifyExit) $RepoRoot $OutDirAbs "inspect_verify_edits_payload" $summaryPath $DryRun.IsPresent
}

$applyArgs = @("-m", "tools.apply_edits", "--repo", $RepoRoot, "--edits", $scaffoldOut, "--artifacts-dir", $OutDirAbs)
if ($DryRun.IsPresent) { $applyArgs += "--dry-run" }
& $pythonCmd @applyArgs
$applyExit = $LASTEXITCODE
if ($applyExit -ne 0) {
  Fail "apply_edits_failed" ("exit_code=" + $applyExit) $RepoRoot $OutDirAbs "inspect_apply_edits_result" $summaryPath $DryRun.IsPresent
}

Write-RunLocalModelArtifacts `
  -ArtifactsDir $OutDirAbs `
  -Status "PASS" `
  -Reason "none" `
  -Detail "ok" `
  -RepoRoot $RepoRoot `
  -Next "review_artifacts_and_run_ci_gates" `
  -SummaryPath $summaryPath `
  -DryRun $DryRun.IsPresent

Write-Host "RUN_LOCAL_MODEL_SUMMARY|status=PASS|next=review_artifacts_and_run_ci_gates"
Write-Host "RUN_LOCAL_MODEL_END"
exit 0
