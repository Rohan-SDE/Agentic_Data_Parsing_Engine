$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path ".venv")) { py -3.12 -m venv .venv; if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12, then retry." } }
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
if (!(Test-Path ".env")) { Copy-Item .env.example .env }
& .\.venv\Scripts\python.exe -m engine.cli migrate
if ($LASTEXITCODE -ne 0) { throw "Migration failed." }
Write-Host "Setup complete. Create your account with: .\.venv\Scripts\python.exe -m engine.cli create-user rohan --admin"
Write-Host "Then run scripts\start-api.ps1 and scripts\start-worker.ps1 in separate terminals."
