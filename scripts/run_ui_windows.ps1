param(
    [switch]$SkipStashPrompt
)

function Get-RepoRoot([string]$startPath) {
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

Set-Location $repoRoot
Write-Host "UI_PREFLIGHT_START|root=$repoRoot"

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "UI_PREFLIGHT_VENV_MISSING|hint=Run: python -m venv .\.venv"
    exit 2
}

$gitStatus = & git -C $repoRoot status --porcelain 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "UI_PREFLIGHT_GIT_UNAVAILABLE|hint=Ensure git is installed and available on PATH."
    exit 2
}

if ($gitStatus) {
    Write-Host "UI_PREFLIGHT_DIRTY"
    Write-Host $gitStatus
    Write-Host "UI_PREFLIGHT_CHOICE|option1=stash_all(option includes untracked)|option2=abort"
    if (-not $SkipStashPrompt) {
        $choice = Read-Host "Type STASH to stash and continue, or ABORT to exit"
    } else {
        $choice = "ABORT"
    }
    if ($choice -eq "STASH") {
        & git -C $repoRoot stash push -u -m "ui_preflight_stash"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "UI_PREFLIGHT_STASH_FAIL|reason=git_stash_failed"
            exit 1
        }
        Write-Host "UI_PREFLIGHT_STASHED"
    } else {
        Write-Host "UI_PREFLIGHT_ABORT"
        exit 1
    }
}

Write-Host "UI_LAUNCH_CMD|$venvPython -m tools.launch_ui"
& $venvPython -m tools.launch_ui
exit $LASTEXITCODE
