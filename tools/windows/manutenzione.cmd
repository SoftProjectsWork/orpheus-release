@echo off
rem Doppio clic: controllo, prova di ripristino, salute di ORPHEUS e report; non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando manutenzione
echo.
pause
