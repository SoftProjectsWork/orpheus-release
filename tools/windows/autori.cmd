@echo off
rem Doppio clic: scrive nel database gli autori proposti da Crossref (authors-v1). Prima fa una copia di sicurezza.
rem Si puo' rilanciare: gli articoli che hanno gia' autori vengono saltati.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando autori
echo.
pause
