@echo off
rem Doppio clic: verifica con Crossref le bibliografie degli articoli completi (regole cite-v2: anno, primo autore, titolo), poi rivaluta senza internet i riferimenti gia' scritti. Copia di sicurezza prima.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando verifica_citazioni
echo.
pause
