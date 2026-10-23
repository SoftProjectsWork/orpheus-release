@echo off
rem Doppio clic: revisione simulata di un manoscritto (.docx) per una rivista (review-v1); il manoscritto resta sul PC; non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando revisione
echo.
pause
