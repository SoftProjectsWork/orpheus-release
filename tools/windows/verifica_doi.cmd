@echo off
rem Doppio clic: verifica con Crossref i DOI dei PDF gia' letti (serve internet). Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando verifica
echo.
pause
