@echo off
rem Doppio clic: cerca in Europe PMC il testo integrale aperto degli articoli completi e salva esito, PMCID, licenze e (solo con licenza Creative Commons) testo e sezioni. Copia di sicurezza prima.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando europepmc
echo.
pause
