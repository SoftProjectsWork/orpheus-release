@echo off
rem Doppio clic: copia di sicurezza nuova del database, poi prova di ripristino in una cartella temporanea (sha256, integrita, catena di audit, migrazioni, conteggi). Solo lettura: non modifica il database.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando prova_ripristino
echo.
pause
