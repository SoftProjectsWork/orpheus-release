@echo off
rem Doppio clic: invia le schede delle riviste scritte da Claude (rapporti\schede_riviste\da_inviare), controllate con le linee guida; poi prepara o legge il file di controllo a mano. Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando schede_riviste
echo.
pause
