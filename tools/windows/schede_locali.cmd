@echo off
rem Doppio clic: prova pilota (ADR-0010) - schede di 10 pacchetti aperti scritte da un modello locale gratuito (Ollama); vanno in rapporti\schede\locali, non nel database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando schede_locali
echo.
pause
