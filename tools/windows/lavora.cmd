@echo off
rem Doppio clic: ORPHEUS fa in ordine tutto quello che manca (lettura dei PDF nuovi, verifiche, riviste, pezzi di testo, vettori, affermazioni e schede scritte da Claude, report). Se il PC si spegne o chiudi la finestra, al prossimo doppio clic riprende da dove si era fermato. Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando lavora
echo.
pause
