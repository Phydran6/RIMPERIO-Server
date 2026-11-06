@echo off
:: ==============================================================================
:: Name:        Phydran6
:: Kontakt:     Phydran6
:: Version:     2026.01.30.13.30.00
:: Beschreibung: RIMPERIO Agent v2026.01.30.13.30.00 - Windows Installer Wrapper
:: ==============================================================================

echo.
echo ==============================================
echo   RIMPERIO Agent v2026.01.30.13.30.00 - Windows
echo ==============================================
echo.

:: Admin-Rechte pruefen
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Dieses Script muss als Administrator ausgefuehrt werden!
    echo.
    echo Rechtsklick auf install-windows.bat -^> "Als Administrator ausfuehren"
    echo.
    pause
    exit /b 1
)

:: PowerShell Script ausfuehren
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*

pause
