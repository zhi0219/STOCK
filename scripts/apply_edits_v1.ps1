param(
  [string]$RepoRoot = "C:\DONE\MONEY\STOCK",
  [string]$EditsPath = "C:\DONE\MONEY\STOCK\artifacts\edits.json",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $RepoRoot

$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { throw "missing_venv_python|py=$Py" }

$Artifacts = Join-Path $RepoRoot "artifacts"
New-Item -Force -ItemType Directory $Artifacts | Out-Null

"APPLY_EDITS_LAUNCH_START|repo=" + $RepoRoot + "|edits=" + $EditsPath + "|dry_run=" + ($DryRun.IsPresent)

$args = @("-m","tools.apply_edits","--repo",$RepoRoot,"--edits",$EditsPath,"--artifacts-dir",$Artifacts)
if ($DryRun.IsPresent) { $args += "--dry-run" }

& $Py @args
if ($LASTEXITCODE -ne 0) { throw "apply_edits_failed|code=$LASTEXITCODE|next=查看 artifacts\apply_edits_result.json" }

"APPLY_EDITS_LAUNCH_OK|result_json=" + (Join-Path $Artifacts "apply_edits_result.json")