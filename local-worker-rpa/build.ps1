# Script PowerShell para empacotar o Worker como .exe standalone
# Uso: .\build.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== auto-adm Worker — Build .exe ===" -ForegroundColor Cyan

# Ativa venv se existir
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

# Instala dependências (incluindo PyInstaller)
poetry install --extras build

Write-Host "Empacotando com PyInstaller..." -ForegroundColor Yellow

pyinstaller `
    --onefile `
    --name "auto-adm-worker" `
    --icon "assets\icon.ico" `
    --add-data ".env.example;." `
    --hidden-import "pywinauto" `
    --hidden-import "pyautogui" `
    --hidden-import "cv2" `
    --hidden-import "pydantic_settings" `
    --collect-submodules "structlog" `
    --noconsole `
    src\main.py

Write-Host "Build concluído! Executável em: dist\auto-adm-worker.exe" -ForegroundColor Green
Write-Host ""
Write-Host "PRÓXIMOS PASSOS:" -ForegroundColor Cyan
Write-Host "  1. Copie dist\auto-adm-worker.exe para a máquina do cliente"
Write-Host "  2. Crie C:\auto-adm\.env com base no .env.example"
Write-Host "  3. Execute: auto-adm-worker.exe"
