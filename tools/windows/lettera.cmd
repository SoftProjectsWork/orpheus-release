@echo off
rem Doppio clic: lettera di presentazione per la rivista (cover-v1), scritta da Claude in sessione e controllata; il manoscritto resta sul PC; non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando lettera
echo.
pause
