@echo off
rem Doppio clic: cerca negli articoli gia' letti, con domande o affermazioni in italiano o in inglese.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando cerca
echo.
pause
