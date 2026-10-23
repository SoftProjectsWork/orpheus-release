@echo off
rem Doppio clic: crea il file da mandare ai colleghi con le schede delle riviste e degli articoli, i metadati e i pezzi di testo (niente PDF, niente manoscritti). Il database non cambia.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando esporta_schede
echo.
pause
