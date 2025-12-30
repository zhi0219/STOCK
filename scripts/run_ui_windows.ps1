Write-Host "UI_LAUNCH_START"

function Get-RepoRoot([string[]]$startPaths) {
    foreach ($startPath in $startPaths) {
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

$startCandidates = @($PSScriptRoot, (Get-Location).Path)
$repoRoot = Get-RepoRoot $startCandidates
if (-not $repoRoot) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=repo_root_not_found|next=Run from the repo root or set your working directory."
    exit 2
}

Set-Location -LiteralPath $repoRoot
Write-Host "UI_PREFLIGHT_START|root=$repoRoot"

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$pythonCmd = $venvPython
if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonCmd = (Get-Command python -ErrorAction SilentlyContinue).Path
}
if (-not $pythonCmd) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=python_not_found|next=python -m venv .\\.venv"
    exit 2
}

& $pythonCmd -m tools.git_health fix --mode safe
if ($LASTEXITCODE -ne 0) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=git_hygiene_blocked|next=python -m tools.git_health report"
    exit 1
}

$gitStatus = & git -C $repoRoot status --porcelain 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=git_unavailable|next=Ensure git is installed and on PATH."
    exit 2
}

if ($gitStatus) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=git_dirty_blocked|next=git status --porcelain"
    Write-Host $gitStatus
    exit 1
}

& git -C $repoRoot pull --ff-only origin main 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=git_pull_failed|next=git pull --ff-only origin main"
    exit 1
}

& $pythonCmd -m tools.ui_preflight --repo-root $repoRoot
if ($LASTEXITCODE -ne 0) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=ui_preflight_failed|next=python -m tools.ui_preflight --repo-root $repoRoot"
    exit 1
}

Write-Host "UI_PREFLIGHT_OK"
Write-Host "UI_LAUNCH_CMD|$pythonCmd -m tools.ui_app"
& $pythonCmd -m tools.ui_app
if ($LASTEXITCODE -ne 0) {
    Write-Host "UI_PREFLIGHT_FAIL|reason=ui_launch_failed|next=python -m tools.ui_app"
    exit $LASTEXITCODE
}

Write-Host "UI_LAUNCH_END"
exit 0
