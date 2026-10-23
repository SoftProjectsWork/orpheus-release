@echo off
rem Doppio clic: invia al database le schede scritte da Claude: ORPHEUS le controlla e accetta solo quelle che superano tutti i controlli. Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando schede
echo.
pause
