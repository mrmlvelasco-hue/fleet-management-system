<#
.SYNOPSIS
  Rebuilds and restarts web + frontend together, in one command --
  for your desktop Docker Desktop setup, after copying in updated
  Flask and/or React files by hand (no git involved).

.USAGE
  Run from inside FMS_Fleet\ (same folder as docker-compose.yml),
  exactly where you already run `docker compose` commands today:

    .\rebuild-desktop.ps1

  Only one side changed? Rebuild just that one, faster:

    .\rebuild-desktop.ps1 -Only web
    .\rebuild-desktop.ps1 -Only frontend

  scheduler shares web's exact image, so a Flask change affects it
  too -- included automatically whenever web rebuilds, unless
  -Only frontend was explicitly given.
#>
param(
    [ValidateSet("web", "frontend", "both")]
    [string]$Only = "both"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\docker-compose.yml")) {
    Write-Host "docker-compose.yml not found here. Run this from inside FMS_Fleet\, the same folder you already run 'docker compose' from." -ForegroundColor Red
    exit 1
}

$services = switch ($Only) {
    "web"      { @("web", "scheduler") }
    "frontend" { @("frontend") }
    "both"     { @("web", "scheduler", "frontend") }
}

Write-Host ""
Write-Host "==> Rebuilding: $($services -join ', ')" -ForegroundColor Cyan
docker compose up -d --build @services
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed -- see the output above for the actual error." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "==> Current status" -ForegroundColor Cyan
docker compose ps

Write-Host ""
Write-Host "==> Last 20 log lines from web (skip if you only rebuilt frontend)" -ForegroundColor Cyan
if ($services -contains "web") {
    docker compose logs web --tail 20
}

Write-Host ""
Write-Host "Done. If 'web' shows Restarting above instead of Up, run:" -ForegroundColor Yellow
Write-Host "    docker compose logs web -f" -ForegroundColor Yellow
Write-Host "and send me that output -- don't guess further on your own." -ForegroundColor Yellow
