Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

function Write-Log([string]$message, [string]$logPath) {
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    $line = "$timestamp $message"
    Write-Host $line
    $logDir = Split-Path $logPath -Parent
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    Add-Content -Path $logPath -Value $line
}

function Get-RepoRoot([string]$startPath) {
    $pythonRoot = & python -m tools.repo_root --print 2>$null
    if ($LASTEXITCODE -eq 0 -and $pythonRoot) {
        return $pythonRoot.Trim()
    }
    $current = Resolve-Path $startPath
    while ($null -ne $current) {
        if (Test-Path (Join-Path $current ".git") -or Test-Path (Join-Path $current "pyproject.toml")) {
            return $current
        }
        $parent = Split-Path $current -Parent
        if ($parent -eq $current) {
            break
        }
        $current = $parent
    }
    return $null
}

$repoRoot = Get-RepoRoot $PSScriptRoot
if (-not $repoRoot) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=repo_root_not_found"
    exit 2
}

$logPath = Join-Path $repoRoot "Logs\runtime\launch_ui_windows_latest.log"
Write-Log "UI_PREFLIGHT_START|root=$repoRoot" $logPath

Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Log "UI_PREFLIGHT_VENV_CREATE" $logPath
    & python -m venv .\.venv
    if ($LASTEXITCODE -ne 0) {
        Write-Log "UI_PREFLIGHT_VENV_FAILED|reason=venv_create_failed" $logPath
        exit 2
    }
    if (Test-Path (Join-Path $repoRoot "requirements-ui.txt")) {
        Write-Log "UI_PREFLIGHT_INSTALL|requirements=requirements-ui.txt" $logPath
        & $venvPython -m pip install -r .\requirements-ui.txt
        if ($LASTEXITCODE -ne 0) {
            Write-Log "UI_PREFLIGHT_INSTALL_FAILED|requirements=requirements-ui.txt" $logPath
            exit 2
        }
    } elseif (Test-Path (Join-Path $repoRoot "requirements.txt")) {
        Write-Log "UI_PREFLIGHT_INSTALL|requirements=requirements.txt" $logPath
        & $venvPython -m pip install -r .\requirements.txt
        if ($LASTEXITCODE -ne 0) {
            Write-Log "UI_PREFLIGHT_INSTALL_FAILED|requirements=requirements.txt" $logPath
            exit 2
        }
    } else {
        Write-Log "UI_PREFLIGHT_INSTALL_SKIPPED|reason=requirements_missing" $logPath
    }
}

Write-Log "UI_LAUNCH_CMD|$venvPython -m tools.launch_ui" $logPath
& $venvPython -m tools.launch_ui
exit $LASTEXITCODE
