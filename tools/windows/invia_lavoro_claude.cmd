@echo off
rem Doppio clic: invia le schede e le affermazioni scritte da Claude, poi pezzi e vettori (gruppo di lavora, copia di sicurezza prima).
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando invia_lavoro_claude
echo.
pause
