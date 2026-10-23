@echo off
rem Doppio clic: articoli nuovi: lettura dei PDF e tutte le verifiche (gruppo di lavora, copia di sicurezza prima).
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando aggiorna_articoli
echo.
pause
