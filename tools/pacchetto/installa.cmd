@echo off
rem Doppio clic: installa (o aggiorna) ORPHEUS in C:\ORPHEUS con Python incluso. Lancialo dalla cartella estratta dallo zip.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installa.ps1"
echo.
pause
