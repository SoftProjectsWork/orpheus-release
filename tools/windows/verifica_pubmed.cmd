@echo off
rem Doppio clic: cerca in PubMed gli articoli completi gia' confermati (PMID, MeSH; serve internet). Prima fa una copia di sicurezza.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando verifica_pubmed
echo.
pause
