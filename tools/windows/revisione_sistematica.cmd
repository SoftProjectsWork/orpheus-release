@echo off
rem Doppio clic: controlla una revisione sistematica o meta-analisi in Word (.docx) della cartella manoscritti, senza rivista (EURYDIKE): flusso PRISMA, numeri dell'abstract, tabella degli studi, k e n, IC, I2, registrazione, risk of bias. Il testo resta su questo PC: nel registro solo numeri ed esiti.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orpheus_comandi.ps1" -Comando revisione_sistematica
echo.
pause
