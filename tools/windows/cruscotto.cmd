@echo off
rem Doppio clic: apre il cruscotto di ORPHEUS nel browser (pagina servita da questo PC, dati solo qui). Dal cruscotto si puo' anche avviare lavora. Chiudi questa finestra per fermarlo.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando cruscotto
echo.
pause
