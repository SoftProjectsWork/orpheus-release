@echo off
rem Doppio clic: un articolo nuovo dai tuoi dati (T-041), una sezione alla volta. I file restano su questo PC.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando nuovo_articolo
echo.
pause
