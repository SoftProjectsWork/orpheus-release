@echo off
rem Doppio clic: scrive nel database le riviste di riviste_prioritarie.csv (tabelle journal e journal_issn, confrontate con Crossref) e collega gli articoli confermati alla loro rivista; poi salva le versioni datate delle linee guida per gli autori gia' lette. Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando riviste
echo.
pause
