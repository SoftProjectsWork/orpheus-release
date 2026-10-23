@echo off
rem Doppio clic: bibliografie degli articoli completi dalle risposte Crossref gia' salvate (senza internet). Solo rapporto: non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando riferimenti
echo.
pause
