@echo off
rem Doppio clic: riviste: elenco, linee guida, schede scritte da Claude e metriche (gruppo di lavora, copia di sicurezza prima).
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando aggiorna_riviste
echo.
pause
