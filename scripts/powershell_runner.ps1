$ErrorActionPreference = "Stop"
$script:Utf8NoBomEncoding = New-Object System.Text.UTF8Encoding $false

function Write-Utf8NoBomText {
  param(
    [string]$Path,
    [string]$Content
  )
  if ($null -eq $Content) { $Content = "" }
  [IO.File]::WriteAllText($Path, $Content, $script:Utf8NoBomEncoding)
}

function Get-PsRunnerRepoRoot {
  param(
    [string[]]$StartPaths
  )
  foreach ($startPath in $StartPaths) {
    if (-not $startPath) {
      continue
    }
    $resolved = Resolve-Path -LiteralPath $startPath -ErrorAction SilentlyContinue
    if (-not $resolved) {
      continue
    }
    $current = $resolved.Path
    while ($current) {
      if ((Test-Path -LiteralPath (Join-Path $current ".git")) -or
          (Test-Path -LiteralPath (Join-Path $current "pyproject.toml")) -or
          (Test-Path -LiteralPath (Join-Path $current "tools")) -or
          (Test-Path -LiteralPath (Join-Path $current "scripts"))) {
        return $current
      }
      $parent = Split-Path -Path $current -Parent
      if ($parent -eq $current) {
        break
      }
      $current = $parent
    }
  }
  return $null
}

function Initialize-PsRunnerArtifacts {
  param(
    [string]$ArtifactsDir
  )
  if (-not (Test-Path -LiteralPath $ArtifactsDir)) {
    New-Item -Force -ItemType Directory -Path $ArtifactsDir | Out-Null
  }
  $paths = [ordered]@{
    SummaryPath = Join-Path $ArtifactsDir "ps_run_summary.json"
    StdoutPath = Join-Path $ArtifactsDir "ps_run_stdout.txt"
    StderrPath = Join-Path $ArtifactsDir "ps_run_stderr.txt"
    MarkersPath = Join-Path $ArtifactsDir "ps_run_markers.txt"
  }
  foreach ($entry in $paths.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value)) {
      Write-Utf8NoBomText -Path $entry.Value -Content ""
    }
  }
  return $paths
}

function Test-PsRunnerArguments {
  param(
    [string[]]$Arguments,
    [ref]$Reason
  )
  if ($null -eq $Arguments) {
    $Reason.Value = "argument_list_null"
    return $false
  }
  if ($Arguments.Count -eq 0) {
    $Reason.Value = "argument_list_empty"
    return $false
  }
  foreach ($arg in $Arguments) {
    if ([string]::IsNullOrWhiteSpace($arg)) {
      $Reason.Value = "argument_list_contains_empty"
      return $false
    }
  }
  return $true
}

function Invoke-PsRunner {
  param(
    [string]$Command,
    [string[]]$Arguments,
    [string]$RepoRoot = "",
    [string]$ArtifactsDir = "",
    [string]$MarkerPrefix = "PS_RUN"
  )
  $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  $startPaths = @($RepoRoot, $PSScriptRoot, (Get-Location).Path)
  if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Get-PsRunnerRepoRoot -StartPaths $startPaths
  }
  if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Get-Location).Path
  }
  $RepoRoot = [IO.Path]::GetFullPath($RepoRoot)

  if ([string]::IsNullOrWhiteSpace($ArtifactsDir)) {
    $ArtifactsDir = Join-Path $RepoRoot "artifacts"
  }
  $ArtifactsDir = if ([IO.Path]::IsPathRooted($ArtifactsDir)) { $ArtifactsDir } else { Join-Path $RepoRoot $ArtifactsDir }
  $ArtifactsDir = [IO.Path]::GetFullPath($ArtifactsDir)

  $paths = Initialize-PsRunnerArtifacts -ArtifactsDir $ArtifactsDir

  $systemDir = [Environment]::SystemDirectory
  $cwd = (Get-Location).Path
  $cwdFull = [IO.Path]::GetFullPath($cwd)
  $systemFull = [IO.Path]::GetFullPath($systemDir)

  $status = "FAIL"
  $exitCode = 2
  $reason = "unknown"
  $commandLine = ""

  $markers = New-Object System.Collections.Generic.List[string]
  $markers.Add("$MarkerPrefix`_START|ts_utc=$ts|cwd=$cwdFull|repo_root=$RepoRoot|command=$Command|artifacts_dir=$ArtifactsDir")
  Write-Host $markers[-1]

  try {
    $shouldRun = $true

    if ($cwdFull -eq $systemFull) {
      if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        $reason = "cwd_system32_without_repo"
        $shouldRun = $false
      } else {
        Set-Location -LiteralPath $RepoRoot
        $cwdFull = [IO.Path]::GetFullPath((Get-Location).Path)
      }
    }

    if ($shouldRun -and $RepoRoot -eq $systemFull) {
      $reason = "repo_root_system32"
      $shouldRun = $false
    }

    if ($shouldRun -and [string]::IsNullOrWhiteSpace($Command)) {
      $reason = "missing_command"
      $shouldRun = $false
    }

    $argReason = ""
    if ($shouldRun -and (-not (Test-PsRunnerArguments -Arguments $Arguments -Reason ([ref]$argReason)))) {
      $reason = $argReason
      $shouldRun = $false
    }

    if ($shouldRun) {
      $commandLine = $Command + " " + ($Arguments -join " ")

      $commandInfo = Get-Command $Command -ErrorAction SilentlyContinue
      if (-not $commandInfo) {
        $reason = "command_not_found"
        $shouldRun = $false
      }
    }

    if ($shouldRun) {
      $process = Start-Process -FilePath $Command -ArgumentList $Arguments -WorkingDirectory $RepoRoot -NoNewWindow -Wait -PassThru -RedirectStandardOutput $paths.StdoutPath -RedirectStandardError $paths.StderrPath
      $exitCode = $process.ExitCode
      if ($exitCode -eq 0) {
        $status = "PASS"
        $reason = "ok"
      } else {
        $status = "FAIL"
        $reason = "exit_nonzero"
      }
    }
  } catch {
    $status = "FAIL"
    $reason = "exception"
    $exitCode = 1
  } finally {

    $summary = [ordered]@{
    status = $status
    reason = $reason
    exit_code = $exitCode
    command = $Command
    args = $Arguments
    command_line = $commandLine
    cwd = $cwdFull
    repo_root = $RepoRoot
    artifacts_dir = $ArtifactsDir
    stdout_path = $paths.StdoutPath
    stderr_path = $paths.StderrPath
    markers_path = $paths.MarkersPath
    ts_utc = $ts
    }
    $summaryJson = $summary | ConvertTo-Json -Depth 6
    Write-Utf8NoBomText -Path $paths.SummaryPath -Content $summaryJson

    $markers.Add("$MarkerPrefix`_SUMMARY|status=$status|reason=$reason|exit_code=$exitCode|stdout=$($paths.StdoutPath)|stderr=$($paths.StderrPath)")
    $markers.Add("$MarkerPrefix`_END")
    Write-Host $markers[-2]
    Write-Host $markers[-1]
    Write-Utf8NoBomText -Path $paths.MarkersPath -Content ($markers -join "`n")
  }

  return [PSCustomObject]@{
    Status = $status
    Reason = $reason
    ExitCode = $exitCode
    StdoutPath = $paths.StdoutPath
    StderrPath = $paths.StderrPath
    SummaryPath = $paths.SummaryPath
    MarkersPath = $paths.MarkersPath
    RepoRoot = $RepoRoot
    ArtifactsDir = $ArtifactsDir
    CommandLine = $commandLine
  }
}
