param(
  [string]$RepoRoot = "C:\DONE\MONEY\STOCK",
  [string]$EditsPath = "C:\DONE\MONEY\STOCK\artifacts\edits.json",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-ApplyEditsArtifacts {
  param(
    [string]$ArtifactsDir,
    [string]$Status,
    [string]$Reason,
    [string]$Detail,
    [string]$InputPath,
    [string]$RepoRoot,
    [bool]$DryRun,
    [string]$Next
  )
  if (-not (Test-Path -LiteralPath $ArtifactsDir)) {
    New-Item -Force -ItemType Directory $ArtifactsDir | Out-Null
  }
  $detailText = ($Detail | Out-String).Trim()
  $payload = @{
    status = $Status
    reason = $Reason
    detail = $detailText
    input_path = $InputPath
    repo_root = $RepoRoot
    dry_run = $DryRun
    next = $Next
  }
  $resultPath = Join-Path $ArtifactsDir "apply_edits_result.json"
  if (-not (Test-Path -LiteralPath $resultPath)) {
    ($payload | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $resultPath -Encoding UTF8
  }
  $errorPath = Join-Path $ArtifactsDir "apply_edits_error.txt"
  $errorLines = @(
    "reason=$Reason",
    "detail=$detailText",
    "input_path=$InputPath",
    "repo_root=$RepoRoot",
    "dry_run=$DryRun",
    "next=$Next"
  )
  ($errorLines -join "`n") | Set-Content -LiteralPath $errorPath -Encoding UTF8
}

function Fail {
  param(
    [string]$Reason,
    [string]$Detail,
    [string]$InputPath,
    [string]$RepoRoot,
    [bool]$DryRun,
    [string]$ArtifactsDir,
    [string]$Next
  )
  Write-ApplyEditsArtifacts `
    -ArtifactsDir $ArtifactsDir `
    -Status "FAIL" `
    -Reason $Reason `
    -Detail $Detail `
    -InputPath $InputPath `
    -RepoRoot $RepoRoot `
    -DryRun $DryRun `
    -Next $Next
  $detailText = ($Detail | Out-String).Trim()
  Write-Host ("APPLY_EDITS_SUMMARY|status=FAIL|reason=" + $Reason + "|detail=" + $detailText + "|result_json=" + (Join-Path $ArtifactsDir "apply_edits_result.json"))
  Write-Host "APPLY_EDITS_END"
  exit 1
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$EditsAbs = if ([IO.Path]::IsPathRooted($EditsPath)) { $EditsPath } else { Join-Path $RepoRoot $EditsPath }
$EditsAbs = [IO.Path]::GetFullPath($EditsAbs)

$Artifacts = Join-Path $RepoRoot "artifacts"
New-Item -Force -ItemType Directory $Artifacts | Out-Null

"APPLY_EDITS_LAUNCH_START|repo=" + $RepoRoot + "|edits=" + $EditsAbs + "|dry_run=" + ($DryRun.IsPresent)

$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { Fail "missing_venv_python" $Py $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "install_venv_python" }

if (-not (Test-Path -LiteralPath $EditsAbs)) {
  Fail "missing_edits_file" $EditsAbs $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "write_edits_json"
}

try {
  $utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
  $stream = [IO.File]::OpenRead($EditsAbs)
  $reader = New-Object IO.StreamReader($stream, $utf8Strict, $true)
  $raw = $reader.ReadToEnd()
  $reader.Close()
  $stream.Close()
} catch {
  Fail "edits_read_failed" $_.Exception.Message $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "fix_utf8_encoding"
}

if ([string]::IsNullOrWhiteSpace($raw)) {
  Fail "edits_empty" "edits_file_is_empty" $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "regenerate_edits"
}

if ($raw -match "```") {
  Fail "markdown_fence_detected" "remove_fences" $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "run_normalize_edits"
}

if ($raw -notmatch '^\s*\{') {
  Fail "leading_prose_detected" "json_must_start_with_object" $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "run_normalize_edits"
}

try {
  $doc = [System.Text.Json.JsonDocument]::Parse($raw)
} catch {
  Fail "json_parse_error" $_.Exception.Message $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "validate_json"
}

if ($doc.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) {
  Fail "json_not_object" "root_must_be_object" $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "regenerate_edits"
}

$root = $doc.RootElement
$versionProp = $null
$createdProp = $null
$editsProp = $null
if (-not $root.TryGetProperty("version", [ref]$versionProp)) {
  Fail "missing_version" "version_required" $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "regenerate_edits"
}
if (-not $root.TryGetProperty("created_at", [ref]$createdProp)) {
  Fail "missing_created_at" "created_at_required" $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "regenerate_edits"
}
if (-not $root.TryGetProperty("edits", [ref]$editsProp)) {
  Fail "missing_edits" "edits_required" $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "regenerate_edits"
}
if ($editsProp.ValueKind -ne [System.Text.Json.JsonValueKind]::Array) {
  Fail "edits_not_array" "edits_must_be_array" $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "regenerate_edits"
}

$args = @("-m", "tools.apply_edits", "--repo", $RepoRoot, "--edits", $EditsAbs, "--artifacts-dir", $Artifacts)
if ($DryRun.IsPresent) { $args += "--dry-run" }

& $Py @args
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
  Fail "apply_edits_failed" ("exit_code=" + $exitCode) $EditsAbs $RepoRoot $DryRun.IsPresent $Artifacts "inspect_apply_edits_result"
}

"APPLY_EDITS_SUMMARY|status=PASS|result_json=" + (Join-Path $Artifacts "apply_edits_result.json")
"APPLY_EDITS_END"
