@echo off
rem Doppio clic: misura la ricerca (recall@5 e @10) sulle domande controllate a mano nel file rapporti\benchmark_ricerca_N_DATA_compilato.csv. Non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando benchmark_ricerca
echo.
pause
