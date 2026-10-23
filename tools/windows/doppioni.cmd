@echo off
rem Doppio clic: possibili doppioni tra gli articoli e proposta degli autori da Crossref (solo rapporto). Non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando doppioni
echo.
pause
