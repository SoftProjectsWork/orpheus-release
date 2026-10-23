@echo off
rem Doppio clic: pagina HTML di stato di ORPHEUS (documenti, copertura per rivista, verifiche, citazioni, schede). Solo lettura: non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando report
echo.
pause
