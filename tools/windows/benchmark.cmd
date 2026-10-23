@echo off
rem Doppio clic: prepara il file per il benchmark umano (50 articoli completi da controllare a mano sui PDF). Non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando benchmark
echo.
pause
