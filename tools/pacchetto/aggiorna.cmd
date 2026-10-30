@echo off
rem Doppio clic: scarica e installa l'ultima versione di ORPHEUS (serve internet). I tuoi dati non vengono toccati.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0aggiorna.ps1"
echo.
pause
