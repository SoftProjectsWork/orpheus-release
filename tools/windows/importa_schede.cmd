@echo off
rem Doppio clic (o trascina sopra il file .zip ricevuto): unisce le schede di un collega al tuo database. Quello che hai gia' resta tuo; prima una prova e una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando importa_schede -File "%~1"
echo.
pause
