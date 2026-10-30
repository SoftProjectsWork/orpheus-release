@echo off
rem Doppio clic: risposta ai revisori (response-v1) dai commenti della rivista e dal manoscritto riveduto; tutto resta sul PC; non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando risposta
echo.
pause
