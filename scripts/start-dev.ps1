[CmdletBinding()]
param(
    [ValidateSet("all", "backend", "frontend")]
    [string]$Service = "all",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$CacheRoot = Join-Path $RepoRoot ".dev-cache"
$FrontendRoot = Join-Path $RepoRoot "frontend"

function Invoke-Checked {
    param(
        [Parameter(Mandatory)]
        [string]$Label,
        [Parameter(Mandatory)]
        [scriptblock]$Command
    )

    Write-Host "==> $Label" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

function Get-CombinedHash {
    param([Parameter(Mandatory)][string[]]$Paths)

    $hashes = foreach ($path in $Paths) {
        (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($hashes -join "`n"))
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return (($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) -join "")
    }
    finally {
        $sha.Dispose()
    }
}

function Sync-Dependencies {
    $createdVenv = $false
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            throw "Python 3.12 was not found in PATH."
        }
        Invoke-Checked "Create Python virtual environment" {
            & $python.Source -m venv (Join-Path $RepoRoot ".venv")
        }
        $createdVenv = $true
    }

    $pythonVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or $pythonVersion.Trim() -ne "3.12") {
        throw "Notify Hub requires Python 3.12; the virtual environment uses $pythonVersion."
    }

    New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
    $backendStamp = Join-Path $CacheRoot "backend-dependencies.sha256"
    $backendHash = Get-CombinedHash @(
        (Join-Path $RepoRoot "pyproject.toml"),
        (Join-Path $RepoRoot "uv.lock")
    )
    $installedBackendHash = if (Test-Path -LiteralPath $backendStamp) {
        (Get-Content -Raw -LiteralPath $backendStamp).Trim()
    }
    else {
        ""
    }
    if ($createdVenv -or $installedBackendHash -ne $backendHash) {
        Invoke-Checked "Install backend dependencies" {
            & $VenvPython -m pip install -e "$RepoRoot[dev]"
        }
        Set-Content -NoNewline -LiteralPath $backendStamp -Value $backendHash
    }

    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npm) {
        throw "Node.js 22 and npm were not found in PATH."
    }
    $nodeVersion = (& node --version).TrimStart("v").Split(".")[0]
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -ne "22") {
        throw "Notify Hub requires Node.js 22."
    }

    $frontendStamp = Join-Path $CacheRoot "frontend-dependencies.sha256"
    $frontendHash = Get-CombinedHash @(
        (Join-Path $FrontendRoot "package.json"),
        (Join-Path $FrontendRoot "package-lock.json")
    )
    $installedFrontendHash = if (Test-Path -LiteralPath $frontendStamp) {
        (Get-Content -Raw -LiteralPath $frontendStamp).Trim()
    }
    else {
        ""
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "node_modules")) -or
        $installedFrontendHash -ne $frontendHash) {
        Push-Location $FrontendRoot
        try {
            Invoke-Checked "Install frontend dependencies" { & $npm.Source ci }
        }
        finally {
            Pop-Location
        }
        Set-Content -NoNewline -LiteralPath $frontendStamp -Value $frontendHash
    }
}

Set-Location $RepoRoot

if ($Service -eq "backend") {
    Write-Host "Notify Hub backend: http://127.0.0.1:8000" -ForegroundColor Green
    & $VenvPython -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
    exit $LASTEXITCODE
}

if ($Service -eq "frontend") {
    Set-Location $FrontendRoot
    Write-Host "Notify Hub frontend: http://127.0.0.1:5173" -ForegroundColor Green
    & npm.cmd run dev -- --host 127.0.0.1 --port 5173
    exit $LASTEXITCODE
}

Sync-Dependencies
Invoke-Checked "Apply database migrations" {
    & $VenvPython -m alembic -c (Join-Path $RepoRoot "backend\alembic.ini") upgrade head
}

$shell = (Get-Process -Id $PID).Path
$commonArguments = @(
    "-NoExit",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"{0}"' -f $PSCommandPath)
)

Start-Process -FilePath $shell -WorkingDirectory $RepoRoot -ArgumentList (
    $commonArguments + @("-Service", "backend")
) | Out-Null
Start-Process -FilePath $shell -WorkingDirectory $RepoRoot -ArgumentList (
    $commonArguments + @("-Service", "frontend")
) | Out-Null

Write-Host ""
Write-Host "Notify Hub development services are starting:" -ForegroundColor Green
Write-Host "  Frontend  http://127.0.0.1:5173"
Write-Host "  Backend   http://127.0.0.1:8000"
Write-Host "  API docs  http://127.0.0.1:8000/docs"
Write-Host "Close the two service windows to stop development servers."

if (-not $NoBrowser) {
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:5173"
}
