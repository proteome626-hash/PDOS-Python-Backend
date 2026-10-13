# PDOS Backend - Windows PowerShell Runner
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "          PDOS Backend - Windows PowerShell Runner          " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python is not installed or not in PATH!" -ForegroundColor Red
    pause
    exit 1
}

# 2. Check and generate .env
if (-not (Test-Path ".env")) {
    Write-Host "[*] Generating secure .env file..." -ForegroundColor Yellow
    $secret = [System.Guid]::NewGuid().ToString("N") + [System.Guid]::NewGuid().ToString("N")
    @"
SECRET_KEY=$secret
DATABASE_URL=sqlite:///./pdos.db
CORS_ORIGINS=*
UVICORN_WORKERS=1
LOG_LEVEL=info
"@ | Out-File -FilePath ".env" -Encoding utf8
    Write-Host "[OK] .env generated." -ForegroundColor Green
}

# 3. Create upload folders
$folders = @("products", "signatures", "receipts", "visits", "profiles")
foreach ($f in $folders) {
    $dir = Join-Path "uploads" $f
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

# 4. Check dependencies
try {
    python -c "import fastapi, uvicorn, sqlalchemy, pydantic" 2>$null
} catch {
    Write-Host "[*] Installing Python packages from requirements.txt..." -ForegroundColor Yellow
    pip install -r requirements.txt
}

# 5. Get Local IP
$wifiIp = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "*Wi-Fi*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress)
if (-not $wifiIp) {
    $wifiIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1 -ExpandProperty IPAddress)
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " [OK] Starting PDOS FastAPI Backend Server!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Local URL:    http://127.0.0.1:8000" -ForegroundColor Cyan
if ($wifiIp) {
    Write-Host "  Network URL:  http://$wifiIp`:8000" -ForegroundColor Cyan
}
Write-Host "  Swagger Docs: http://127.0.0.1:8000/docs" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Press Ctrl + C to stop the server.`n" -ForegroundColor DarkGray

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
