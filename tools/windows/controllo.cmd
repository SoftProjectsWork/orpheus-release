@echo off
rem Doppio clic: controllo generale di ORPHEUS (orpheus doctor).
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando controllo
echo.
pause
