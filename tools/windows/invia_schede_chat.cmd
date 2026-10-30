@echo off
rem Doppio clic: mette nel database le schede della chat gratuita gia' accettate (stessi controlli delle schede di Claude; prima una copia di sicurezza).
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando invia_schede_chat
echo.
pause
