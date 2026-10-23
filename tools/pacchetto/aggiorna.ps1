# aggiorna.ps1 - scarica e installa l'ultima versione di ORPHEUS dal repository pubblico (T-053).
# Doppio clic su C:\ORPHEUS\aggiorna.cmd. Serve internet; niente git, niente account.
#   1. chiede a GitHub l'ultima versione pubblicata (SoftProjectsWork/orpheus-release, pagina Releases)
#   2. se e' piu' nuova di quella installata scarica lo zip ORPHEUS_<versione>_windows.zip
#   3. lo estrae in una cartella temporanea e lancia il suo installa.ps1 -Aggiornamento
# I dati (database, PDF, schede) non vengono toccati; il database viene aggiornato con una copia di sicurezza prima.
# -Pacchetto <zip>: installa quel file senza chiedere a GitHub (prove, o zip ricevuto a mano).

param([string]$Pacchetto = '')

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Repo = 'SoftProjectsWork/orpheus-release'
$Root = if ($env:ORPHEUS_INSTALL_ROOT) { $env:ORPHEUS_INSTALL_ROOT } else { $PSScriptRoot }
$current = (Get-Content (Join-Path $Root 'VERSIONE.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $current) { $current = '0' }
Write-Host "ORPHEUS installato: versione $current"

function Version-Of([string]$v) {
    $v = $v.Trim().TrimStart('v', 'V')
    try { return [version]$v } catch { return [version]'0.0' }
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ('orpheus_aggiornamento_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
    if (-not $Pacchetto) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $headers = @{ 'User-Agent' = 'ORPHEUS-aggiorna'; 'Accept' = 'application/vnd.github+json' }
        try {
            $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers $headers
        } catch {
            Write-Host "Non riesco a contattare GitHub ($($_.Exception.Message)). Controlla internet e riprova."
            exit 2
        }
        $latest = "$($rel.tag_name)".TrimStart('v', 'V')
        Write-Host "Ultima versione pubblicata: $latest"
        if ((Version-Of $latest) -le (Version-Of $current)) { Write-Host "Hai gia' l'ultima versione: niente da fare."; exit 0 }
        $asset = @($rel.assets | Where-Object { $_.name -like 'ORPHEUS_*_windows.zip' }) | Select-Object -First 1
        if (-not $asset) { Write-Host "La versione $latest non ha ancora il pacchetto per Windows: riprova tra qualche minuto."; exit 2 }
        $Pacchetto = Join-Path $work $asset.name
        Write-Host ("Scarico {0} ({1:N0} MB)..." -f $asset.name, ($asset.size / 1MB))
        $ProgressPreference = 'SilentlyContinue'      # molto piu' veloce in Windows PowerShell 5.1
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $Pacchetto -Headers @{ 'User-Agent' = 'ORPHEUS-aggiorna' }
    }
    Write-Host 'Estraggo il pacchetto...'
    Expand-Archive -Path $Pacchetto -DestinationPath $work -Force
    $inst = Get-ChildItem -Path $work -Recurse -Filter 'installa.ps1' | Select-Object -First 1
    if (-not $inst) { Write-Host "Il pacchetto non contiene installa.ps1: non e' un pacchetto di ORPHEUS."; exit 2 }
    Write-Host 'Chiudi le finestre di ORPHEUS (cruscotto compreso) se sono aperte.'
    & $inst.FullName -Aggiornamento
    exit $LASTEXITCODE
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
