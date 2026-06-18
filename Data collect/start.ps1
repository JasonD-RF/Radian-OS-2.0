# start.ps1 — Launch Radian OS 2.0 on Windows (Docker via WSL2)
#
# Usage (from PowerShell in the "Data collect" folder):
#   .\start.ps1           — start all services
#   .\start.ps1 -Stop     — kill supervisor and web server
#   .\start.ps1 -Status   — show what's running
#
param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Config     = Join-Path $ScriptDir "config\collectors.local.yaml"
$Python     = Join-Path $ScriptDir "venv\Scripts\python.exe"
$LogDir     = Join-Path $ScriptDir "logs"
$ComposeFile = Join-Path (Split-Path $ScriptDir -Parent) "docker-compose.yml"

# ── Helpers ────────────────────────────────────────────────────────────────────

function Check-Config {
    if (-not (Test-Path $Config)) {
        Write-Host "ERROR: $Config not found." -ForegroundColor Red
        Write-Host "Copy the example and fill in your values:"
        Write-Host "  copy config\collectors.local.yaml.example config\collectors.local.yaml"
        exit 1
    }
}

function Check-Venv {
    if (-not (Test-Path $Python)) {
        Write-Host "Creating Python virtual environment..."
        python -m venv "$ScriptDir\venv"
        & $Python -m pip install --quiet --upgrade pip
        & $Python -m pip install --quiet -r "$ScriptDir\requirements.txt"
        Write-Host "Venv ready."
    }
}

function Start-DockerWSL {
    $dockerRunning = wsl -d Ubuntu-24.04 -u root -- docker info 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Starting Docker in WSL2..."
        wsl -d Ubuntu-24.04 -u root -- systemctl start docker
        Start-Sleep -Seconds 3
    }
}

function Start-DB {
    Write-Host "Starting TimescaleDB..."
    $wslPath = $ComposeFile -replace "\\", "/" -replace "C:", "/mnt/c"
    wsl -d Ubuntu-24.04 -u root -- docker compose -f `"$wslPath`" up -d
    Write-Host "Waiting for database to be healthy..."
    for ($i = 0; $i -lt 30; $i++) {
        $status = wsl -d Ubuntu-24.04 -u root -- docker ps --filter "name=radianos-db-1" --format "{{.Status}}" 2>$null
        if ($status -like "*healthy*") { break }
        Start-Sleep -Seconds 1
    }
    Write-Host "Database ready."
}

function Apply-Schema {
    Write-Host "Applying schema (safe to run on existing DB)..."
    $schemaPath = Join-Path $ScriptDir "schema.sql"
    $wslSchema  = $schemaPath -replace "\\", "/" -replace "C:", "/mnt/c"
    wsl -d Ubuntu-24.04 -u root -- bash -c "docker exec -i radianos-db-1 psql -U radian -d radian_forge < '$wslSchema'"
}

function Get-ServicePid {
    param($ProcessName)
    $procs = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue
    if ($procs) { return $procs[0].Id } else { return $null }
}

function Stop-Services {
    Write-Host "Stopping supervisor and web server..."
    Get-Process -Name "python" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*src.supervisor*" -or $_.CommandLine -like "*src.web.server*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "Services stopped."
}

function Show-Status {
    Write-Host "`n=== Docker ===" -ForegroundColor Cyan
    $result = wsl -d Ubuntu-24.04 -u root -- docker ps --format "table {{.Names}}`t{{.Status}}`t{{.Ports}}" 2>$null
    if ($result) { Write-Host $result } else { Write-Host "  (docker not running)" }

    Write-Host "`n=== Python Services ===" -ForegroundColor Cyan
    $pythonProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue
    if ($pythonProcs) {
        $pythonProcs | ForEach-Object { Write-Host "  PID $($_.Id) — $($_.Path)" }
    } else {
        Write-Host "  (no python processes running)"
    }
    Write-Host "`nDashboard: http://localhost:8765`n"
}

# ── Commands ───────────────────────────────────────────────────────────────────

if ($Stop) {
    Stop-Services
    exit 0
}

if ($Status) {
    Show-Status
    exit 0
}

# Default: start everything
Check-Config
Check-Venv
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Start-DockerWSL
Start-DB
Apply-Schema

Write-Host "Starting supervisor..."
$sup = Start-Process -FilePath $Python `
    -ArgumentList "-m", "src.supervisor", "--config", $Config `
    -WorkingDirectory $ScriptDir `
    -WindowStyle Normal `
    -PassThru
Write-Host "  Supervisor PID: $($sup.Id)"

Write-Host "Starting web server..."
$web = Start-Process -FilePath $Python `
    -ArgumentList "-m", "src.web.server", "--config", $Config `
    -WorkingDirectory $ScriptDir `
    -WindowStyle Normal `
    -PassThru
Write-Host "  Web server PID: $($web.Id)"

Start-Sleep -Seconds 3
Show-Status
Start-Process "http://localhost:8765"
