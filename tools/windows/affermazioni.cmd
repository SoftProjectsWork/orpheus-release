@echo off
rem Doppio clic: invia le affermazioni scritte da Claude (rapporti\affermazioni\da_inviare), controllate con il PDF, con il livello della prova; poi prepara o legge il file di controllo a mano. Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando affermazioni
echo.
pause
