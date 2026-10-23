@echo off
rem Doppio clic: file per controllare a mano 10 schede a sorte; se il file compilato c'e' gia', calcola la precisione. Non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando controllo_schede
echo.
pause
