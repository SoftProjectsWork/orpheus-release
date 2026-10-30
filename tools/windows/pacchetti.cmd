@echo off
rem Doppio clic: prepara i pacchetti di lavoro per le schede degli articoli; chiede la priorita' (una rivista, equo, casuale) e quanti. Claude li legge in sessione.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando pacchetti
echo.
pause
