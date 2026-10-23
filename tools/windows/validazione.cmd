@echo off
rem Doppio clic: validazione della Fase 7 su un manoscritto vero (validation-v1): file di controllo a mano e precisione con IC 95%; il manoscritto resta sul PC; non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando validazione
echo.
pause
