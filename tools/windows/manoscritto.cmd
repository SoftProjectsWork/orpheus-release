@echo off
rem Doppio clic: controlla un manoscritto Word (.docx) della cartella manoscritti rispetto alle regole delle riviste. Il testo resta su questo PC: nel registro solo numeri ed esiti. Il rapporto completo si apre nel browser.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando manoscritto
echo.
pause
