@echo off
rem Doppio clic: scrive nel database le metriche ufficiali delle riviste: SJR, quartili e h-index dal file di SCImago messo in inbox\metriche (file scimagojr ANNO.csv) e citazioni medie a 2 anni e h-index da OpenAlex (internet). Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando metriche_riviste
echo.
pause
