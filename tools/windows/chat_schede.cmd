@echo off
rem Doppio clic: schede degli articoli con una chat gratuita (emergenza, ADR-0011). Guida passo per passo con gli appunti: copia i messaggi, legge la risposta della chat, copia la correzione. Non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando chat_schede
echo.
pause
