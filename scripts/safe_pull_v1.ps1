param(
  [string]$RepoRoot = "",
  [string]$ArtifactsDir = "",
  [ValidateSet("dry_run","apply")]
  [string]$Mode = "",
  [bool]$DryRun = $true,
  [bool]$AllowStash = $true,
  [bool]$IncludeUntracked = $false,
  [bool]$RequireClean = $false,
  [bool]$AutoSwitchToMain = $true,
  [string]$ExpectedUpstream = "origin/main",
  [string]$ExpectedRemotePattern = "^(https?://|git@)",
  [bool]$AllowDetached = $false,
  [int]$LockTimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "powershell_runner.ps1")

[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

$script:Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$script:MarkersPath = ""
$script:OutPath = ""
$script:ErrPath = ""
$script:ArtifactsRoot = ""
$script:ArtifactsDir = ""
$script:ArtifactsRel = ""
$script:ArtifactsRunRoot = ""
$script:RepoRoot = ""
$script:GitExe = ""
$script:GitVersion = ""
$script:Warnings = New-Object System.Collections.Generic.List[string]
$script:DecisionTrace = [ordered]@{
  inputs = [ordered]@{}
  decisions = @()
  actions = @()
}
$script:RunId = ""
$script:RunPayload = [ordered]@{}
$script:RunPhases = New-Object System.Collections.Generic.List[object]
$script:PhaseSeen = @{}
$script:CurrentPhase = "init"
$script:StepEmitted = [ordered]@{
  precheck = $false
  lock = $false
  stash = $false
  fetch = $false
  pull = $false
  postcheck = $false
}
$script:LockPath = ""
$script:LockAcquired = $false
$script:FinalExitCode = 0
$script:StopSignal = "SAFE_PULL_STOP"
$script:ContractVersion = 20250214

function Get-UtcTimestamp {
  return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function OD-Set {
  param(
    [hashtable]$Dict,
    [string]$Key,
    $Value
  )
  if ($null -eq $Dict) { return }
  $Dict[$Key] = $Value
}

function OD-Get {
  param(
    [hashtable]$Dict,
    [string]$Key,
    $Default
  )
  if ($null -eq $Dict) { return $Default }
  if ($Dict.Contains($Key)) { return $Dict[$Key] }
  return $Default
}

function Normalize-RelPath {
  param(
    [string]$Path
  )
  if ([string]::IsNullOrWhiteSpace($Path)) { return "" }
  return ($Path -replace "\\", "/")
}

function Join-RelPath {
  param(
    [string]$Left,
    [string]$Right
  )
  if ([string]::IsNullOrWhiteSpace($Left)) { return (Normalize-RelPath -Path $Right) }
  if ([string]::IsNullOrWhiteSpace($Right)) { return (Normalize-RelPath -Path $Left) }
  return (Normalize-RelPath -Path (Join-Path $Left $Right))
}

function Add-RunPhase {
  param(
    [string]$Phase,
    [string]$Status
  )
  if ([string]::IsNullOrWhiteSpace($Phase)) { return }
  if ($script:PhaseSeen.ContainsKey($Phase)) {
    if ($Status -ne "FAIL") { return }
  } else {
    $script:PhaseSeen[$Phase] = $true
  }
  $entry = [ordered]@{
    phase = $Phase
    status = $Status
    ts_utc = Get-UtcTimestamp
  }
  $script:RunPhases.Add($entry) | Out-Null
}

function Write-RunArtifact {
  $runPath = Join-Path $script:ArtifactsDir "safe_pull_run.json"
  OD-Set -Dict $script:RunPayload -Key "phases" -Value @($script:RunPhases)
  $runJson = $script:RunPayload | ConvertTo-Json -Depth 8
  Write-TextArtifact -Path $runPath -Content $runJson
}

function Get-ArtifactPointer {
  param(
    [string]$FileName
  )
  if ([string]::IsNullOrWhiteSpace($script:ArtifactsRel)) {
    return $FileName
  }
  return (Join-RelPath -Left $script:ArtifactsRel -Right $FileName)
}

function Get-ArtifactPointerForPath {
  param(
    [string]$FullPath
  )
  if ([string]::IsNullOrWhiteSpace($FullPath)) { return "" }
  if ([string]::IsNullOrWhiteSpace($script:ArtifactsDir)) { return $FullPath }
  $full = [IO.Path]::GetFullPath($FullPath)
  $runRoot = [IO.Path]::GetFullPath($script:ArtifactsDir)
  if ($full.StartsWith($runRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    $relative = $full.Substring($runRoot.Length).TrimStart("\", "/")
    return (Join-RelPath -Left $script:ArtifactsRel -Right $relative)
  }
  return $FullPath
}

function Emit-RunStart {
  param(
    [string]$RepoRoot,
    [string]$Cwd,
    [string]$Mode,
    [string]$ArtifactsDir
  )
  $runTs = OD-Get -Dict $script:RunPayload -Key "ts_utc" -Default ""
  $line = "SAFE_PULL_RUN_START|run_id=$($script:RunId)|ts_utc=$runTs|repo_root=$RepoRoot|cwd=$Cwd|mode=$Mode|artifacts_dir=$ArtifactsDir"
  Write-Marker $line
}

function Emit-RunEnd {
  param(
    [string]$Status,
    [string]$Next
  )
  $line = "SAFE_PULL_RUN_END|run_id=$($script:RunId)|status=$Status|next=$Next"
  Write-Marker $line
}

function Initialize-Artifacts {
  param(
    [string]$ArtifactsDir
  )
  if (-not (Test-Path -LiteralPath $ArtifactsDir)) {
    New-Item -ItemType Directory -Force -Path $ArtifactsDir | Out-Null
  }
  $script:MarkersPath = Join-Path $ArtifactsDir "safe_pull_markers.txt"
  $script:OutPath = Join-Path $ArtifactsDir "safe_pull_out.txt"
  $script:ErrPath = Join-Path $ArtifactsDir "safe_pull_err.txt"
  foreach ($path in @($script:MarkersPath, $script:OutPath, $script:ErrPath)) {
    if (-not (Test-Path -LiteralPath $path)) {
      Write-TextArtifact -Path $path -Content ""
    }
  }
}

function Write-Log {
  param(
    [string]$Line
  )
  if ($null -eq $Line) { $Line = "" }
  Append-TextArtifact -Path $script:OutPath -Content $Line
  Write-Output $Line
}

function Write-ErrLog {
  param(
    [string]$Line
  )
  if ($null -eq $Line) { $Line = "" }
  Append-TextArtifact -Path $script:ErrPath -Content $Line
  Write-Output $Line
}

function Write-Marker {
  param(
    [string]$Line
  )
  if ($null -eq $Line) { $Line = "" }
  Append-TextArtifact -Path $script:MarkersPath -Content $Line
  Write-Log $Line
}

function Resolve-GitExe {
  $gitCmd = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $gitCmd -or -not $gitCmd.Source) { return $null }
  return $gitCmd.Source
}

function Resolve-GitStateBlocks {
  param(
    [string]$RepoRoot
  )
  $gitDir = Join-Path $RepoRoot ".git"
  $statePaths = @(
    (Join-Path $gitDir "MERGE_HEAD"),
    (Join-Path $gitDir "CHERRY_PICK_HEAD"),
    (Join-Path $gitDir "REVERT_HEAD"),
    (Join-Path $gitDir "rebase-apply"),
    (Join-Path $gitDir "rebase-merge"),
    (Join-Path $gitDir "AM")
  )
  $blocked = New-Object System.Collections.Generic.List[string]
  foreach ($path in $statePaths) {
    if (Test-Path -LiteralPath $path) {
      $blocked.Add([IO.Path]::GetFileName($path))
    }
  }
  return $blocked
}

function Run-Git {
  param(
    [string]$GitExe,
    [string]$RepoRoot,
    [string]$ArtifactsDir,
    [string]$MarkerPrefix,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
  )
  $runResult = Invoke-PsRunner -Command $GitExe -Arguments $Args -RepoRoot $RepoRoot -ArtifactsDir $ArtifactsDir -MarkerPrefix $MarkerPrefix
  $stdoutText = ""
  if (Test-Path -LiteralPath $runResult.StdoutPath) {
    try {
      $stdoutText = Get-Content -Raw -LiteralPath $runResult.StdoutPath -ErrorAction Stop
    } catch {
      Write-ErrLog "SAFE_PULL_INTERNAL|reason=git_stdout_read_failed|path=$($runResult.StdoutPath)"
      throw
    }
  }
  if ($null -eq $stdoutText) { $stdoutText = "" }
  $stderrText = ""
  if (Test-Path -LiteralPath $runResult.StderrPath) {
    try {
      $stderrText = Get-Content -Raw -LiteralPath $runResult.StderrPath -ErrorAction Stop
    } catch {
      Write-ErrLog "SAFE_PULL_INTERNAL|reason=git_stderr_read_failed|path=$($runResult.StderrPath)"
      throw
    }
  }
  if ($null -eq $stderrText) { $stderrText = "" }
  $combinedText = [string]::Concat($stdoutText, $stderrText).Trim()
  return [PSCustomObject]@{
    ExitCode = $runResult.ExitCode
    StdoutPath = $runResult.StdoutPath
    StderrPath = $runResult.StderrPath
    Stdout = $stdoutText
    Stderr = $stderrText
    Combined = $combinedText
    CommandLine = $runResult.CommandLine
  }
}

function Write-TextArtifact {
  param(
    [string]$Path,
    [string]$Content
  )
  if ([string]::IsNullOrWhiteSpace($Path)) { return }
  $parent = Split-Path -Parent $Path
  if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  [IO.File]::WriteAllText($Path, $Content, $script:Utf8NoBom)
}

function Append-TextArtifact {
  param(
    [string]$Path,
    [string]$Content
  )
  if ([string]::IsNullOrWhiteSpace($Path)) { return }
  $parent = Split-Path -Parent $Path
  if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  $line = $Content + [Environment]::NewLine
  [IO.File]::AppendAllText($Path, $line, $script:Utf8NoBom)
}

function Resolve-AllowedArtifactsDir {
  param(
    [string]$RepoRoot,
    [string]$ArtifactsDir
  )
  $baseArtifacts = Join-Path $RepoRoot "artifacts"
  $baseArtifacts = [IO.Path]::GetFullPath($baseArtifacts)
  $resolved = if ([string]::IsNullOrWhiteSpace($ArtifactsDir)) { $baseArtifacts } else { $ArtifactsDir }
  $resolved = if ([IO.Path]::IsPathRooted($resolved)) { $resolved } else { Join-Path $RepoRoot $resolved }
  $resolved = [IO.Path]::GetFullPath($resolved)
  if (-not $resolved.StartsWith($baseArtifacts, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "artifacts_dir_outside_allowlist"
  }
  return $resolved
}

function Get-RepoRelativePath {
  param(
    [string]$RepoRoot,
    [string]$FullPath
  )
  $root = [IO.Path]::GetFullPath($RepoRoot).TrimEnd("\", "/")
  $full = [IO.Path]::GetFullPath($FullPath)
  if ($full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
    $relative = $full.Substring($root.Length).TrimStart("\", "/")
    if ([string]::IsNullOrWhiteSpace($relative)) { return "." }
    return $relative
  }
  return $full
}

function Emit-PrecheckMarker {
  param(
    [string]$Branch,
    [int]$Detached,
    [string]$Upstream,
    [string]$UpstreamStatus,
    [int]$Porcelain,
    [int]$Untracked,
    [int]$Ahead,
    [int]$Behind,
    [int]$Diverged
  )
  $line = "SAFE_PULL_PRECHECK|branch=$Branch|detached=$Detached|upstream=$Upstream|upstream_status=$UpstreamStatus|porcelain=$Porcelain|untracked=$Untracked|ahead=$Ahead|behind=$Behind|diverged=$Diverged"
  Write-Marker $line
  $script:StepEmitted.precheck = $true
}

function Emit-LockMarker {
  param(
    [string]$Status,
    [string]$Path,
    [string]$Owner,
    [string]$Stale
  )
  $line = "SAFE_PULL_LOCK|status=$Status|path=$Path|owner=$Owner|stale=$Stale"
  Write-Marker $line
  $script:StepEmitted.lock = $true
}

function Emit-StashMarker {
  param(
    [string]$Status,
    [string]$Ref,
    [int]$IncludesUntracked,
    [string]$Message
  )
  $line = "SAFE_PULL_STASH|status=$Status|ref=$Ref|includes_untracked=$IncludesUntracked|message=$Message"
  Write-Marker $line
  $script:StepEmitted.stash = $true
}

function Emit-FetchMarker {
  param(
    [string]$Status,
    [int]$ExitCode,
    [string]$StdoutPath,
    [string]$StderrPath
  )
  $line = "SAFE_PULL_FETCH|status=$Status|exit=$ExitCode|stdout=$StdoutPath|stderr=$StderrPath"
  Write-Marker $line
  $script:StepEmitted.fetch = $true
}

function Emit-PullMarker {
  param(
    [string]$Status,
    [int]$ExitCode,
    [string]$StdoutPath,
    [string]$StderrPath,
    [string]$Reason
  )
  $line = "SAFE_PULL_PULL_FF_ONLY|status=$Status|exit=$ExitCode|stdout=$StdoutPath|stderr=$StderrPath|reason=$Reason"
  Write-Marker $line
  $script:StepEmitted.pull = $true
}

function Emit-PostcheckMarker {
  param(
    [int]$Porcelain,
    [string]$Branch,
    [string]$Upstream,
    [int]$Ahead,
    [int]$Behind,
    [int]$Diverged
  )
  $line = "SAFE_PULL_POSTCHECK|porcelain=$Porcelain|branch=$Branch|upstream=$Upstream|ahead=$Ahead|behind=$Behind|diverged=$Diverged"
  Write-Marker $line
  $script:StepEmitted.postcheck = $true
}

function Emit-MissingMarkers {
  if (-not $script:StepEmitted.precheck) {
    Emit-PrecheckMarker -Branch "" -Detached 0 -Upstream "" -UpstreamStatus "" -Porcelain 0 -Untracked 0 -Ahead 0 -Behind 0 -Diverged 0
  }
  if (-not $script:StepEmitted.lock) {
    Emit-LockMarker -Status "SKIP" -Path "" -Owner "" -Stale "0"
  }
  if (-not $script:StepEmitted.stash) {
    Emit-StashMarker -Status "SKIP" -Ref "" -IncludesUntracked 0 -Message "not_run"
  }
  if (-not $script:StepEmitted.fetch) {
    Emit-FetchMarker -Status "SKIP" -ExitCode 0 -StdoutPath "" -StderrPath ""
  }
  if (-not $script:StepEmitted.pull) {
    Emit-PullMarker -Status "SKIP" -ExitCode 0 -StdoutPath "" -StderrPath "" -Reason "not_run"
  }
  if (-not $script:StepEmitted.postcheck) {
    Emit-PostcheckMarker -Porcelain 0 -Branch "" -Upstream "" -Ahead 0 -Behind 0 -Diverged 0
  }
}

function Write-SummaryAndStop {
  param(
    [string]$Status,
    [string]$Reason,
    [string]$Next,
    [hashtable]$SummaryPayload,
    [int]$ExitCode,
    [string]$Phase,
    [string]$EvidenceArtifact
  )
  if ([string]::IsNullOrWhiteSpace($Next)) { $Next = "none" }
  if ([string]::IsNullOrWhiteSpace($EvidenceArtifact)) { $EvidenceArtifact = $Next }
  OD-Set -Dict $SummaryPayload -Key "status" -Value $Status
  OD-Set -Dict $SummaryPayload -Key "reason" -Value $Reason
  OD-Set -Dict $SummaryPayload -Key "next" -Value $Next
  OD-Set -Dict $SummaryPayload -Key "phase" -Value $Phase
  OD-Set -Dict $SummaryPayload -Key "run_id" -Value $script:RunId
  OD-Set -Dict $SummaryPayload -Key "evidence_artifact" -Value $EvidenceArtifact
  OD-Set -Dict $SummaryPayload -Key "warnings" -Value @($script:Warnings)
  OD-Set -Dict $SummaryPayload -Key "artifacts_dir" -Value $script:ArtifactsRel
  OD-Set -Dict $SummaryPayload -Key "artifacts_dir_abs" -Value $script:ArtifactsDir
  OD-Set -Dict $SummaryPayload -Key "contract_version" -Value $script:ContractVersion
  if (-not $SummaryPayload.ContainsKey("mode")) { OD-Set -Dict $SummaryPayload -Key "mode" -Value (if ($DryRun) { "dry_run" } else { "apply" }) }
  if (-not $SummaryPayload.ContainsKey("dry_run")) { OD-Set -Dict $SummaryPayload -Key "dry_run" -Value $DryRun }
  if (-not $SummaryPayload.ContainsKey("what_would_change_my_mind")) {
    OD-Set -Dict $SummaryPayload -Key "what_would_change_my_mind" -Value "If CI passes ps51+ps7 and verifier PASS, accept. If artifacts write outside -ArtifactsDir, reject."
  }

  $summaryPath = Join-Path $script:ArtifactsDir "safe_pull_summary.json"
  $summaryJson = $SummaryPayload | ConvertTo-Json -Depth 8
  Write-TextArtifact -Path $summaryPath -Content $summaryJson

  $decisionPath = Join-Path $script:ArtifactsDir "decision_trace.json"
  $decisionJson = $script:DecisionTrace | ConvertTo-Json -Depth 8
  Write-TextArtifact -Path $decisionPath -Content $decisionJson

  OD-Set -Dict $script:RunPayload -Key "status" -Value $Status
  OD-Set -Dict $script:RunPayload -Key "reason" -Value $Reason
  OD-Set -Dict $script:RunPayload -Key "next" -Value $Next
  Add-RunPhase -Phase $Phase -Status $Status
  Write-RunArtifact
  Emit-MissingMarkers
  $summaryMode = OD-Get -Dict $SummaryPayload -Key "mode" -Default (if ($DryRun) { "dry_run" } else { "apply" })
  $summaryNotes = OD-Get -Dict $SummaryPayload -Key "notes" -Default ""
  $summaryLine = "SAFE_PULL_SUMMARY|status=$Status|reason=$Reason|phase=$Phase|next=$Next|run_id=$($script:RunId)|mode=$summaryMode|notes=$summaryNotes|artifacts_dir=$($script:ArtifactsRel)|evidence=$EvidenceArtifact"
  Write-Marker $summaryLine
  Emit-RunEnd -Status $Status -Next $Next
  Write-Marker "SAFE_PULL_END"
  if ($Status -ne "PASS") {
    Write-ErrLog ("SAFE_PULL_FAIL|reason=" + $Reason + "|phase=" + $Phase + "|next=" + $Next)
  }
  $script:FinalExitCode = $ExitCode
  throw $script:StopSignal
}

try {
  $ts = Get-UtcTimestamp
  $cwdFull = [IO.Path]::GetFullPath((Get-Location).Path)
  $systemDir = [Environment]::SystemDirectory
  $systemFull = [IO.Path]::GetFullPath($systemDir)
  $script:RunId = "$ts-$PID"

  $resolvedMode = if (-not [string]::IsNullOrWhiteSpace($Mode)) { $Mode } else { if ($DryRun) { "dry_run" } else { "apply" } }
  $DryRun = $resolvedMode -eq "dry_run"

  $script:GitExe = Resolve-GitExe
  $provisionalRoot = if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $cwdFull } else { [IO.Path]::GetFullPath($RepoRoot) }
  try {
    $script:ArtifactsRoot = Resolve-AllowedArtifactsDir -RepoRoot $provisionalRoot -ArtifactsDir $ArtifactsDir
  } catch {
    $line = "SAFE_PULL_EXCEPTION|phase=init|message=artifacts_dir_outside_allowlist|artifact=none"
    Write-Output $line
    [Console]::Error.WriteLine($line)
    exit 1
  }
  $script:ArtifactsRoot = [IO.Path]::GetFullPath($script:ArtifactsRoot)
  $script:ArtifactsRunRoot = Join-Path $script:ArtifactsRoot "safe_pull"
  $script:ArtifactsDir = Join-Path $script:ArtifactsRunRoot $script:RunId
  Initialize-Artifacts -ArtifactsDir $script:ArtifactsDir
  Write-TextArtifact -Path (Join-Path $script:ArtifactsRunRoot "_latest.txt") -Content $script:ArtifactsDir
  $script:ArtifactsRel = Normalize-RelPath -Path (Join-Path "safe_pull" $script:RunId)

  $mode = $resolvedMode
  $script:RunPayload = [ordered]@{
    run_id = $script:RunId
    ts_utc = $ts
    repo_root = $provisionalRoot
    cwd = $cwdFull
    mode = $mode
    git_path = $script:GitExe
    artifacts_dir = $script:ArtifactsRel
    artifacts_dir_abs = $script:ArtifactsDir
    contract_version = $script:ContractVersion
    policy_flags = [ordered]@{
      allow_stash = $AllowStash
      include_untracked = $IncludeUntracked
      require_clean = $RequireClean
      auto_switch_to_main = $AutoSwitchToMain
      expected_upstream = $ExpectedUpstream
      allow_detached = $AllowDetached
      lock_timeout_seconds = $LockTimeoutSeconds
    }
    status = "IN_PROGRESS"
    reason = ""
    next = ""
    phases = @()
  }
  Emit-RunStart -RepoRoot $provisionalRoot -Cwd $cwdFull -Mode $mode -ArtifactsDir $script:ArtifactsRel
  Write-RunArtifact
  OD-Set -Dict $script:DecisionTrace -Key "inputs" -Value ([ordered]@{
      repo_root = $RepoRoot
      artifacts_dir = $ArtifactsDir
      mode = $mode
      dry_run = $DryRun
      allow_stash = $AllowStash
      include_untracked = $IncludeUntracked
      require_clean = $RequireClean
      auto_switch_to_main = $AutoSwitchToMain
      expected_upstream = $ExpectedUpstream
      expected_remote_pattern = $ExpectedRemotePattern
      allow_detached = $AllowDetached
      lock_timeout_seconds = $LockTimeoutSeconds
    })

  if ($cwdFull -eq $systemFull -or $provisionalRoot -eq $systemFull) {
    Write-Marker ("SAFE_PULL_START|ts_utc=" + $ts + "|repo_root=unknown|cwd=" + $cwdFull + "|git=missing|mode=" + $mode)
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
    Write-SummaryAndStop -Status "FAIL" -Reason "system32_guard" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "init" -EvidenceArtifact $next
  }

  if (-not $script:GitExe) {
    Write-Marker ("SAFE_PULL_START|ts_utc=" + $ts + "|repo_root=unknown|cwd=" + $cwdFull + "|git=missing|mode=" + $mode)
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
    Write-SummaryAndStop -Status "FAIL" -Reason "git_not_found" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "init" -EvidenceArtifact $next
  }

  $gitVersionResult = Run-Git -GitExe $script:GitExe -RepoRoot $provisionalRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_GIT_VERSION" --version
  $script:GitVersion = if ($gitVersionResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($gitVersionResult.Combined)) { $gitVersionResult.Combined } else { "unknown" }

  $rootResult = Run-Git -GitExe $script:GitExe -RepoRoot $provisionalRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_REV_PARSE" rev-parse --show-toplevel
  if ($rootResult.ExitCode -ne 0) {
    Write-Marker ("SAFE_PULL_START|ts_utc=" + $ts + "|repo_root=unknown|cwd=" + $cwdFull + "|git=" + $script:GitExe + "|mode=" + $mode)
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
    Write-SummaryAndStop -Status "FAIL" -Reason "not_in_git_repo" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "init" -EvidenceArtifact $next
  }
  $script:RepoRoot = [IO.Path]::GetFullPath($rootResult.Combined)

  Write-Marker ("SAFE_PULL_START|ts_utc=" + $ts + "|repo_root=" + $script:RepoRoot + "|cwd=" + $cwdFull + "|git=" + $script:GitExe + "|mode=" + $mode)

  if (-not [string]::IsNullOrWhiteSpace($RepoRoot)) {
    $requestedRoot = [IO.Path]::GetFullPath($RepoRoot)
    if ($requestedRoot -ne $script:RepoRoot) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
      Write-SummaryAndStop -Status "FAIL" -Reason "repo_root_mismatch" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "init" -EvidenceArtifact $next
    }
  }

  if ($cwdFull -ne $script:RepoRoot) {
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
    Write-SummaryAndStop -Status "FAIL" -Reason "not_at_repo_root" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "init" -EvidenceArtifact $next
  }

  try {
    $resolvedRoot = Resolve-AllowedArtifactsDir -RepoRoot $script:RepoRoot -ArtifactsDir $ArtifactsDir
  } catch {
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
    Write-SummaryAndStop -Status "FAIL" -Reason "artifacts_dir_outside_allowlist" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "init" -EvidenceArtifact $next
  }
  $resolvedRoot = [IO.Path]::GetFullPath($resolvedRoot)
  if ($resolvedRoot -ne $script:ArtifactsRoot) {
    $script:ArtifactsRoot = $resolvedRoot
    $script:ArtifactsRunRoot = Join-Path $script:ArtifactsRoot "safe_pull"
    $script:ArtifactsDir = Join-Path $script:ArtifactsRunRoot $script:RunId
    Initialize-Artifacts -ArtifactsDir $script:ArtifactsDir
    Write-TextArtifact -Path (Join-Path $script:ArtifactsRunRoot "_latest.txt") -Content $script:ArtifactsDir
    $script:ArtifactsRel = Normalize-RelPath -Path (Join-Path "safe_pull" $script:RunId)
  }
  OD-Set -Dict $script:RunPayload -Key "repo_root" -Value $script:RepoRoot
  OD-Set -Dict $script:RunPayload -Key "artifacts_dir" -Value $script:ArtifactsRel
  OD-Set -Dict $script:RunPayload -Key "artifacts_dir_abs" -Value $script:ArtifactsDir
  Write-RunArtifact

  $configSnapshotPath = Join-Path $script:ArtifactsDir "config_snapshot.txt"
  $originUrl = ""
  $originResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_REMOTE" remote get-url origin
  if ($originResult.ExitCode -eq 0) {
    $originUrl = $originResult.Combined
  }
  $gitConfigResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_CONFIG" config --local --list
  $configLines = @(
    "git_version=" + $script:GitVersion,
    "origin_url=" + $originUrl,
    "config_local=",
    $gitConfigResult.Stdout
  )
  Write-TextArtifact -Path $configSnapshotPath -Content ($configLines -join "`n")

  if ([string]::IsNullOrWhiteSpace($originUrl)) {
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "config_snapshot.txt"
    Write-SummaryAndStop -Status "FAIL" -Reason "origin_url_missing" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "init" -EvidenceArtifact $next
  }

  if (-not ($originUrl -match $ExpectedRemotePattern)) {
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "config_snapshot.txt"
    Write-SummaryAndStop -Status "FAIL" -Reason "origin_url_mismatch" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "init" -EvidenceArtifact $next
  }

  $script:CurrentPhase = "precheck"
  $branchResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_BRANCH" rev-parse --abbrev-ref HEAD
  $branchName = if ($branchResult.ExitCode -eq 0) { $branchResult.Combined } else { "" }
  $branchHeadPath = Join-Path $script:ArtifactsDir "safe_pull_precheck_head.txt"
  Write-TextArtifact -Path $branchHeadPath -Content $branchResult.Combined
  if ($branchName -eq "HEAD" -or [string]::IsNullOrWhiteSpace($branchName)) {
    $branchFallback = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_BRANCH_FALLBACK" symbolic-ref -q --short HEAD
    if ($branchFallback.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($branchFallback.Combined)) {
      $branchName = $branchFallback.Combined
    }
  }
  $detached = if ($branchName -eq "HEAD" -or [string]::IsNullOrWhiteSpace($branchName)) { 1 } else { 0 }

  if ($detached -eq 1 -and -not $AllowDetached) {
    Emit-PrecheckMarker -Branch $branchName -Detached $detached -Upstream "" -UpstreamStatus "NO_UPSTREAM" -Porcelain 0 -Untracked 0 -Ahead 0 -Behind 0 -Diverged 0
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_precheck_head.txt"
    Write-SummaryAndStop -Status "FAIL" -Reason "detached_head" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
  }

  # git status --porcelain
  $statusBefore = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_STATUS_BEFORE" status --porcelain
  $statusBranch = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_STATUS_BRANCH" status --branch --porcelain
  Write-TextArtifact -Path (Join-Path $script:ArtifactsDir "git_status_before.txt") -Content $statusBranch.Stdout
  Write-TextArtifact -Path (Join-Path $script:ArtifactsDir "git_porcelain_before.txt") -Content $statusBefore.Stdout

  $revBefore = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_REV_BEFORE" rev-parse HEAD
  Write-TextArtifact -Path (Join-Path $script:ArtifactsDir "git_rev_before.txt") -Content $revBefore.Combined

  $unmergedResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_LS_FILES" ls-files -u
  if ($unmergedResult.ExitCode -ne 0) {
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
    Write-SummaryAndStop -Status "FAIL" -Reason "git_unmerged_check_failed" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
  }
  if (-not [string]::IsNullOrWhiteSpace($unmergedResult.Combined)) {
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
    Write-SummaryAndStop -Status "FAIL" -Reason "unmerged_paths" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
  }

  $blockedStates = Resolve-GitStateBlocks -RepoRoot $script:RepoRoot
  if ($blockedStates.Count -gt 0) {
    $blockedText = ($blockedStates | Sort-Object) -join ","
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_run.json"
    Write-SummaryAndStop -Status "FAIL" -Reason ("git_state_present:" + $blockedText) -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
  }

  $porcelainLines = @()
  if (-not [string]::IsNullOrWhiteSpace($statusBefore.Stdout)) {
    $porcelainLines = $statusBefore.Stdout.Split("`n") | ForEach-Object { $_.TrimEnd() } | Where-Object { $_ }
  }
  $untrackedLines = $porcelainLines | Where-Object { $_ -like "??*" }
  $trackedLines = $porcelainLines | Where-Object { $_ -notlike "??*" }

  $upstream = ""
  $upstreamStatus = "NO_UPSTREAM"
  $ahead = 0
  $behind = 0
  $diverged = 0

  $upstreamResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_UPSTREAM" rev-parse --abbrev-ref --symbolic-full-name "@{u}"
  $upstreamPath = Join-Path $script:ArtifactsDir "safe_pull_precheck_upstream.txt"
  $upstreamContent = "exit_code=$($upstreamResult.ExitCode)`nstdout=$($upstreamResult.Stdout)`nstderr=$($upstreamResult.Stderr)"
  Write-TextArtifact -Path $upstreamPath -Content $upstreamContent
  if ($upstreamResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($upstreamResult.Combined)) {
    $upstream = $upstreamResult.Combined
    $upstreamStatus = "OK"
  } else {
    $script:Warnings.Add("no_upstream_configured")
  }

  $aheadBehindPath = Join-Path $script:ArtifactsDir "safe_pull_precheck_ahead_behind.txt"
  if (-not [string]::IsNullOrWhiteSpace($upstream)) {
    $revListResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_REV_LIST" rev-list --left-right --count "@{u}...HEAD"
    Write-TextArtifact -Path $aheadBehindPath -Content $revListResult.Combined
    if ($revListResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($revListResult.Combined)) {
      $parts = $revListResult.Combined.Split("`t")
      if ($parts.Count -ge 2) {
        $ahead = [int]$parts[0]
        $behind = [int]$parts[1]
      }
    }
  } else {
    Write-TextArtifact -Path $aheadBehindPath -Content "skipped_no_upstream"
  }

  if ($ahead -gt 0 -and $behind -gt 0) { $diverged = 1 }

  Emit-PrecheckMarker -Branch $branchName -Detached $detached -Upstream $upstream -UpstreamStatus $upstreamStatus -Porcelain $porcelainLines.Count -Untracked $untrackedLines.Count -Ahead $ahead -Behind $behind -Diverged $diverged

  if ($diverged -eq 1) {
    $summary = [ordered]@{ ts_utc = $ts }
    $next = Get-ArtifactPointer -FileName "safe_pull_precheck_ahead_behind.txt"
    Write-SummaryAndStop -Status "FAIL" -Reason "diverged_branch" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
  }

  if ($untrackedLines.Count -gt 0) {
    $untrackedPath = Join-Path $script:ArtifactsDir "git_untracked_before.txt"
    $untrackedSample = $untrackedLines | Select-Object -First 200
    Write-TextArtifact -Path $untrackedPath -Content ($untrackedSample -join "`n")
    if (-not $IncludeUntracked) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName "git_untracked_before.txt"
      Write-SummaryAndStop -Status "FAIL" -Reason "untracked_files_present" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
    }
  }

  if ($trackedLines.Count -gt 0) {
    if ($RequireClean) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName "git_porcelain_before.txt"
      Write-SummaryAndStop -Status "FAIL" -Reason "dirty_worktree" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
    }
    if ($DryRun) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName "git_porcelain_before.txt"
      Write-SummaryAndStop -Status "FAIL" -Reason "dirty_worktree_dry_run" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
    }
    if (-not $AllowStash) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName "git_porcelain_before.txt"
      Write-SummaryAndStop -Status "FAIL" -Reason "dirty_worktree_no_stash" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "precheck" -EvidenceArtifact $next
    }
  }

  $script:DecisionTrace.decisions += [ordered]@{
    step = "precheck"
    branch = $branchName
    detached = $detached
    upstream = $upstream
    upstream_status = $upstreamStatus
    ahead = $ahead
    behind = $behind
    diverged = $diverged
    porcelain = $porcelainLines.Count
    untracked = $untrackedLines.Count
  }
  Add-RunPhase -Phase "precheck" -Status "OK"

  $script:CurrentPhase = "lock"
  $lockDir = Join-Path $script:ArtifactsDir "locks"
  if (-not (Test-Path -LiteralPath $lockDir)) {
    New-Item -ItemType Directory -Force -Path $lockDir | Out-Null
  }
  $script:LockPath = Join-Path $lockDir "safe_pull_v1.lock"
  $lockOwner = ""
  $staleFlag = "0"

  if (Test-Path -LiteralPath $script:LockPath) {
    $lockContent = Get-Content -Raw -LiteralPath $script:LockPath -ErrorAction SilentlyContinue
    $lockData = $null
    try {
      $lockData = $lockContent | ConvertFrom-Json -ErrorAction Stop
    } catch {
      $lockData = $null
    }
    if ($lockData -and $lockData.ts_utc) {
      $lockOwner = [string]$lockData.host
      $lockTs = [datetime]::Parse($lockData.ts_utc)
      $ageSeconds = [int]([datetime]::UtcNow - $lockTs).TotalSeconds
      $pidAlive = $false
      if ($lockData.pid) {
        try {
          $proc = Get-Process -Id $lockData.pid -ErrorAction Stop
          if ($proc) { $pidAlive = $true }
        } catch {
          $pidAlive = $false
        }
      }
      if ($ageSeconds -gt $LockTimeoutSeconds -and -not $pidAlive) {
        $staleFlag = "1"
        Remove-Item -LiteralPath $script:LockPath -Force -ErrorAction SilentlyContinue
      } else {
        Emit-LockMarker -Status "FAIL" -Path $script:LockPath -Owner $lockOwner -Stale $staleFlag
        $summary = [ordered]@{ ts_utc = $ts }
        $next = Get-ArtifactPointer -FileName (Join-Path "locks" "safe_pull_v1.lock")
        Write-SummaryAndStop -Status "FAIL" -Reason "lock_exists" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "lock" -EvidenceArtifact $next
      }
    } else {
      Emit-LockMarker -Status "FAIL" -Path $script:LockPath -Owner "unknown" -Stale "0"
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName (Join-Path "locks" "safe_pull_v1.lock")
      Write-SummaryAndStop -Status "FAIL" -Reason "lock_parse_failed" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "lock" -EvidenceArtifact $next
    }
  }

  $lockPayload = [ordered]@{
    pid = $PID
    host = $env:COMPUTERNAME
    ts_utc = $ts
    command = $MyInvocation.Line
  }
  $lockJson = $lockPayload | ConvertTo-Json -Depth 4
  Write-TextArtifact -Path $script:LockPath -Content $lockJson
  $script:LockAcquired = $true
  Emit-LockMarker -Status "OK" -Path $script:LockPath -Owner $env:COMPUTERNAME -Stale $staleFlag
  Add-RunPhase -Phase "lock" -Status "OK"

  $script:CurrentPhase = "stash"
  $stashRef = ""
  $stashMessage = ""
  $stashIncludesUntracked = 0
  if ($trackedLines.Count -gt 0) {
    $stashMessage = "safe_pull_pre_" + $ts
    $stashArgs = @("stash", "push", "-m", $stashMessage)
    if ($IncludeUntracked) {
      $stashArgs = @("stash", "push", "-u", "-m", $stashMessage)
      $stashIncludesUntracked = 1
    }
    $stashResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_STASH_PUSH" @stashArgs
    if ($stashResult.ExitCode -ne 0) {
      Emit-StashMarker -Status "FAIL" -Ref "" -IncludesUntracked $stashIncludesUntracked -Message $stashMessage
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointerForPath -FullPath $stashResult.StderrPath
      Write-SummaryAndStop -Status "FAIL" -Reason "git_stash_failed" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "stash" -EvidenceArtifact $next
    }
    $stashMatch = [regex]::Match($stashResult.Combined, "stash@\{\d+\}")
    if ($stashMatch.Success) { $stashRef = $stashMatch.Value }
    if (-not [string]::IsNullOrWhiteSpace($stashRef)) {
      Write-TextArtifact -Path (Join-Path $script:ArtifactsDir "stash_ref.txt") -Content $stashRef
    }
    Emit-StashMarker -Status "OK" -Ref $stashRef -IncludesUntracked $stashIncludesUntracked -Message $stashMessage
    Add-RunPhase -Phase "stash" -Status "OK"
  } else {
    Emit-StashMarker -Status "SKIP" -Ref "" -IncludesUntracked 0 -Message "clean"
    Add-RunPhase -Phase "stash" -Status "SKIP"
  }

  $expectedBranch = ""
  if ($ExpectedUpstream -and $ExpectedUpstream.Contains("/")) {
    $expectedBranch = $ExpectedUpstream.Split("/")[1]
  }
  if ($AutoSwitchToMain -and -not [string]::IsNullOrWhiteSpace($expectedBranch) -and $branchName -ne $expectedBranch) {
    if ($DryRun) {
      $script:Warnings.Add("dry_run_skip_switch")
    } else {
      $switchResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_SWITCH" checkout $expectedBranch
      if ($switchResult.ExitCode -ne 0) {
        $summary = [ordered]@{ ts_utc = $ts }
        $next = Get-ArtifactPointerForPath -FullPath $switchResult.StderrPath
        Write-SummaryAndStop -Status "FAIL" -Reason "git_switch_failed" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "switch" -EvidenceArtifact $next
      }
      $branchName = $expectedBranch
    }
  }

  $remoteName = $ExpectedUpstream.Split("/")[0]
  $script:CurrentPhase = "fetch"
  if ($DryRun) {
    $fetchResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_FETCH" fetch --prune --dry-run $remoteName
    $fetchStatus = if ($fetchResult.ExitCode -eq 0) { "OK" } else { "FAIL" }
    Emit-FetchMarker -Status $fetchStatus -ExitCode $fetchResult.ExitCode -StdoutPath $fetchResult.StdoutPath -StderrPath $fetchResult.StderrPath
    Add-RunPhase -Phase "fetch" -Status $fetchStatus
  } else {
    $fetchResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_FETCH" fetch --prune $remoteName
    $fetchStatus = if ($fetchResult.ExitCode -eq 0) { "OK" } else { "FAIL" }
    Emit-FetchMarker -Status $fetchStatus -ExitCode $fetchResult.ExitCode -StdoutPath $fetchResult.StdoutPath -StderrPath $fetchResult.StderrPath
    Add-RunPhase -Phase "fetch" -Status $fetchStatus
    if ($fetchResult.ExitCode -ne 0) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointerForPath -FullPath $fetchResult.StderrPath
      Write-SummaryAndStop -Status "FAIL" -Reason "git_fetch_failed" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "fetch" -EvidenceArtifact $next
    }
  }

  if ($DryRun) {
    Emit-PullMarker -Status "SKIP" -ExitCode 0 -StdoutPath "" -StderrPath "" -Reason "dry_run"
    Add-RunPhase -Phase "pull" -Status "SKIP"
  } else {
    $script:CurrentPhase = "pull"
    $pullResult = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_PULL" -c pull.ff=only -c pull.rebase=false pull --ff-only
    $pullStatus = if ($pullResult.ExitCode -eq 0) { "OK" } else { "FAIL" }
    $reason = if ($pullResult.ExitCode -eq 0) { "fast_forward" } else { "ff_only_failed" }
    Emit-PullMarker -Status $pullStatus -ExitCode $pullResult.ExitCode -StdoutPath $pullResult.StdoutPath -StderrPath $pullResult.StderrPath -Reason $reason
    Add-RunPhase -Phase "pull" -Status $pullStatus
    if ($pullResult.ExitCode -ne 0) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointerForPath -FullPath $pullResult.StderrPath
      Write-SummaryAndStop -Status "FAIL" -Reason "git_pull_ff_only_failed" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "pull" -EvidenceArtifact $next
    }
  }

  $script:CurrentPhase = "postcheck"
  $statusAfter = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_STATUS_AFTER" status --porcelain
  $statusBranchAfter = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_STATUS_BRANCH_AFTER" status --branch --porcelain
  Write-TextArtifact -Path (Join-Path $script:ArtifactsDir "git_status_after.txt") -Content $statusBranchAfter.Stdout
  Write-TextArtifact -Path (Join-Path $script:ArtifactsDir "git_porcelain_after.txt") -Content $statusAfter.Stdout

  $revAfter = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_REV_AFTER" rev-parse HEAD
  Write-TextArtifact -Path (Join-Path $script:ArtifactsDir "git_rev_after.txt") -Content $revAfter.Combined

  $postLines = @()
  if (-not [string]::IsNullOrWhiteSpace($statusAfter.Stdout)) {
    $postLines = $statusAfter.Stdout.Split("`n") | ForEach-Object { $_.TrimEnd() } | Where-Object { $_ }
  }
  $postAhead = $ahead
  $postBehind = $behind
  $postDiverged = $diverged

  $revListAfter = Run-Git -GitExe $script:GitExe -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_REV_LIST_AFTER" rev-list --left-right --count ("HEAD..." + $ExpectedUpstream)
  if ($revListAfter.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($revListAfter.Combined)) {
    $partsAfter = $revListAfter.Combined.Split("`t")
    if ($partsAfter.Count -ge 2) {
      $postAhead = [int]$partsAfter[0]
      $postBehind = [int]$partsAfter[1]
    }
  }
  if ($postAhead -gt 0 -and $postBehind -gt 0) { $postDiverged = 1 } else { $postDiverged = 0 }

  Emit-PostcheckMarker -Porcelain $postLines.Count -Branch $branchName -Upstream $upstream -Ahead $postAhead -Behind $postBehind -Diverged $postDiverged
  Add-RunPhase -Phase "postcheck" -Status "OK"

  if ($DryRun) {
    if ($statusBefore.Stdout -ne $statusAfter.Stdout) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName "git_status_before.txt"
      Write-SummaryAndStop -Status "FAIL" -Reason "dry_run_modified_worktree" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "postcheck" -EvidenceArtifact $next
    }
  }

  if (-not $DryRun) {
    if ($postLines.Count -gt 0) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName "git_porcelain_after.txt"
      Write-SummaryAndStop -Status "FAIL" -Reason "dirty_worktree_after_pull" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "postcheck" -EvidenceArtifact $next
    }
    if ($postBehind -ne 0) {
      $summary = [ordered]@{ ts_utc = $ts }
      $next = Get-ArtifactPointer -FileName "safe_pull_precheck_ahead_behind.txt"
      Write-SummaryAndStop -Status "FAIL" -Reason "behind_upstream_after_pull" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase "postcheck" -EvidenceArtifact $next
    }
  }

  $summaryStatus = "PASS"
  $summaryNext = "none"
  $summaryNotes = ""
  if ($DryRun) {
    $summaryNotes = "fetch/pull_skipped"
  }

  $pythonCmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pythonCmd -and $pythonCmd.Source) {
    $script:CurrentPhase = "verify_inventory"
    $verifyInventory = Run-Git -GitExe $pythonCmd.Source -RepoRoot $script:RepoRoot -ArtifactsDir $script:ArtifactsDir -MarkerPrefix "SAFE_PULL_VERIFY_INVENTORY" -m tools.verify_inventory_contract --artifacts-dir $script:ArtifactsDir
    if ($verifyInventory.ExitCode -ne 0) {
      $summaryStatus = "DEGRADED"
      $summaryNext = "run win_inventory_refresh_v1"
      $script:Warnings.Add("inventory_contract_failed")
    }
  } else {
    $script:Warnings.Add("python_missing_inventory_check")
  }

  $summaryPayload = [ordered]@{
    ts_utc = $ts
    status = $summaryStatus
    reason = "ok"
    next = $summaryNext
    contract_version = $script:ContractVersion
    mode = $mode
    dry_run = $DryRun
    notes = $summaryNotes
    allow_stash = $AllowStash
    include_untracked = $IncludeUntracked
    require_clean = $RequireClean
    auto_switch_to_main = $AutoSwitchToMain
    expected_upstream = $ExpectedUpstream
    repo_root = $script:RepoRoot
    cwd = $cwdFull
    git_exe = $script:GitExe
    git_version = $script:GitVersion
    branch = $branchName
    upstream = $upstream
    ahead = $postAhead
    behind = $postBehind
    diverged = $postDiverged
    what_would_change_my_mind = "If CI passes ps51+ps7 and verifier PASS, accept. If artifacts write outside -ArtifactsDir, reject."
  }

  Write-SummaryAndStop -Status $summaryStatus -Reason "ok" -Next $summaryNext -SummaryPayload $summaryPayload -ExitCode 0 -Phase "summary" -EvidenceArtifact (Get-ArtifactPointer -FileName "safe_pull_summary.json")
} catch {
  if ($_.Exception.Message -ne $script:StopSignal) {
    $exceptionPhase = $script:CurrentPhase
    $exceptionPath = Join-Path $script:ArtifactsDir "safe_pull_exception.json"
    $exceptionTextPath = Join-Path $script:ArtifactsDir "safe_pull_exception.txt"
    $exceptionPayload = [ordered]@{
      run_id = $script:RunId
      ts_utc = Get-UtcTimestamp
      phase = $exceptionPhase
      type = $_.Exception.GetType().FullName
      message = $_.Exception.Message
      stack = $_.ScriptStackTrace
      repo_root = $script:RepoRoot
      cwd = (Get-Location).Path
      mode = if ($DryRun) { "dry_run" } else { "apply" }
      git_path = $script:GitExe
    }
    $exceptionJson = "{}"
    try {
      $exceptionJson = $exceptionPayload | ConvertTo-Json -Depth 6
    } catch {
      $exceptionJson = "{}"
    }
    try {
      Write-TextArtifact -Path $exceptionPath -Content $exceptionJson
    } catch {
    }
    try {
      Write-TextArtifact -Path $exceptionTextPath -Content $exceptionJson
    } catch {
    }
    $exceptionRel = Get-ArtifactPointer -FileName "safe_pull_exception.json"
    try {
      Write-Marker ("SAFE_PULL_EXCEPTION|run_id=" + $script:RunId + "|phase=" + $exceptionPhase + "|type=" + $exceptionPayload.type + "|message=" + $exceptionPayload.message + "|artifact=" + $exceptionRel)
    } catch {
    }
    $summary = [ordered]@{ ts_utc = (Get-UtcTimestamp) }
    $next = Get-ArtifactPointer -FileName "safe_pull_exception.txt"
    try {
      Write-SummaryAndStop -Status "FAIL" -Reason "internal_exception" -Next $next -SummaryPayload $summary -ExitCode 1 -Phase $exceptionPhase -EvidenceArtifact $next
    } catch {
      $line = "SAFE_PULL_EXCEPTION|run_id=$($script:RunId)|phase=$exceptionPhase|message=summary_write_failed|artifact=$next"
      Write-Output $line
      [Console]::Error.WriteLine($line)
      $script:FinalExitCode = 1
    }
  }
} finally {
  if ($script:LockAcquired -and (Test-Path -LiteralPath $script:LockPath)) {
    Remove-Item -LiteralPath $script:LockPath -Force -ErrorAction SilentlyContinue
  }
  exit $script:FinalExitCode
}
