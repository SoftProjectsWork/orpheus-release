@echo off
rem Doppio clic: legge i PDF nuovi della inbox e fa il controllo di qualita'.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando leggi
echo.
pause
