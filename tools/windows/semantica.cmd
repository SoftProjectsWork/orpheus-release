@echo off
rem Doppio clic: scrive i pezzi di testo degli articoli completi e i loro vettori con il modello multilingue locale (la prima volta lo scarica, circa 0,5 GB). Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando semantica
echo.
pause
