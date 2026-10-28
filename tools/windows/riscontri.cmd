@echo off
rem Doppio clic: commenti di editor e revisori delle tue sottomissioni: invia le classificazioni scritte da Claude e prepara i pacchetti delle lettere da classificare. Le lettere restano sul PC. Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando riscontri
echo.
pause
