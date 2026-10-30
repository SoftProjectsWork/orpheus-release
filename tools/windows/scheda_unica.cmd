@echo off
rem Doppio clic: scheda unica dell'articolo (cards-v2, EURYDIKE). Copia le schede cards-v1 senza cambiarle (prima una
rem copia di sicurezza) ed esporta tutte le schede in rapporti\schede_v2 (JSON + CSV, i valori corretti a mano segnati).
rem Si puo' rilanciare: le schede gia' convertite vengono saltate e le tue correzioni non si toccano.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando scheda_unica
echo.
pause
