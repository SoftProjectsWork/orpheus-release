@echo off
rem Doppio clic: metodi statistici, trasparenza e struttura degli articoli completi (con pagina e frase), poi il file per il controllo a mano di un campione o, se compilato, la precisione. Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando metodi
echo.
pause
