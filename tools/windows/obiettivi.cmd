@echo off
rem Doppio clic: obiettivo e disegno degli articoli (scheda unica cards-v2): invia le schede scritte da Claude e prepara i pacchetti nuovi (prima sport, esercizio fisico e biomeccanica). Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando obiettivi
echo.
pause
