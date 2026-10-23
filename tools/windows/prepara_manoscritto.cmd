@echo off
rem Doppio clic: un manoscritto per una rivista: controllo, revisione simulata, lettera e validazione; il manoscritto resta sul PC; non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando prepara_manoscritto
echo.
pause
