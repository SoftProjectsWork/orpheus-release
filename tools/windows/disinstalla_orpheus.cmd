@echo off
rem Doppio clic: toglie ORPHEUS da questo PC (cartelle del programma e del PC in AppData\Local, vecchia C:\ORPHEUS, collegamenti). Prima mostra cosa toglie e chiede di scrivere DISINSTALLA. La cartella dei dati (ORPHEUS_DATI) non viene mai toccata; le copie di sicurezza del database si possono conservare.
rem Lo script gira da una copia nella cartella temporanea, in una finestra sua: questa cartella si puo' cancellare.
chcp 65001 >nul
copy /y "%~dp0disinstalla_orpheus.ps1" "%TEMP%\orpheus_disinstalla.ps1" >nul
start "ORPHEUS - disinstallazione" powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\orpheus_disinstalla.ps1" -Copia
exit /b
