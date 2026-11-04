# installa.ps1 - installa o aggiorna ORPHEUS dal pacchetto pubblico (Python incluso; niente git, niente account).
# Si lancia con doppio clic su installa.cmd, nella cartella estratta dallo zip ORPHEUS_<versione>_windows.zip.
# aggiorna.cmd lo lancia da solo con -Aggiornamento (nessuna domanda se il PC e' gia' configurato).
#
# Cosa fa, in ordine:
#   0. (una volta) porta le impostazioni, i registri e le copie di sicurezza di una vecchia installazione in C:\ORPHEUS
#   1. copia Python e il codice in %LOCALAPPDATA%\Programs\ORPHEUS (python\ e orpheus\); il file .env del PC resta com'e'
#   2. la prima volta chiede la cartella dei dati (predefinita C:\ORPHEUS_DATI) e l'e-mail per Crossref/OpenAlex
#   3. se non c'e' un database lo crea (orpheus init); se c'e' lo aggiorna (orpheus migrate, copia di sicurezza prima)
#   4. copia i comandi a doppio clic nella cartella del programma; collegamenti nel menu Start e sul desktop
#   5. controllo generale (orpheus doctor)
# I dati (database, PDF, schede) non vengono mai toccati dall'aggiornamento.
# Tutto quello che compare a schermo viene scritto anche in %LOCALAPPDATA%\ORPHEUS\installazione_log.txt.
# Cartelle dal 2026-11-04 (0.76.0): programma in %LOCALAPPDATA%\Programs\ORPHEUS, registri e copie di sicurezza in
# %LOCALAPPDATA%\ORPHEUS (niente amministratore); prima tutto in C:\ORPHEUS, che si toglie alla fine.
# -Da: cartella da cui e' partito aggiorna.cmd (se e' la vecchia C:\ORPHEUS, aggiorna.cmd resta fino alla prossima volta).

param([switch]$Aggiornamento, [string]$Da = '')

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
# Variabili solo per i test e per la prova automatica del pacchetto (GitHub Actions).
$Root    = if ($env:ORPHEUS_INSTALL_ROOT) { $env:ORPHEUS_INSTALL_ROOT } else { Join-Path $env:LOCALAPPDATA 'Programs\ORPHEUS' }
$State   = if ($env:ORPHEUS_STATE_DIR) { $env:ORPHEUS_STATE_DIR } elseif ($env:ORPHEUS_INSTALL_ROOT) { $Root } else { Join-Path $env:LOCALAPPDATA 'ORPHEUS' }
$Legacy  = if ($env:ORPHEUS_LEGACY_ROOT) { $env:ORPHEUS_LEGACY_ROOT } elseif ($env:ORPHEUS_INSTALL_ROOT) { '' } else { 'C:\ORPHEUS' }
$Here    = $PSScriptRoot
$App     = Join-Path $Here 'app'
$CodeDir = Join-Path $Root 'orpheus'
$PyDir   = Join-Path $Root 'python'
foreach ($d in @($Root, $State)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
$LogFile = Join-Path $State 'installazione_log.txt'
Set-Content -Path $LogFile -Value "ORPHEUS - installazione dal pacchetto - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding UTF8

function Write-Log([string]$Text) { Write-Host $Text; Add-Content -Path $LogFile -Value $Text -Encoding UTF8 }
function Stop-Install([string]$Text) {
    Write-Log ''; Write-Log "INTERROTTO: $Text"; Write-Log "Registro: $LogFile"; exit 1
}
function Invoke-Py([string[]]$ArgList) {
    $out = & $script:Py -m orpheus @ArgList 2>&1 | Out-String
    $code = $LASTEXITCODE
    Add-Content -Path $LogFile -Value ("> orpheus " + ($ArgList -join ' ') + "   (codice $code)`n" + $out) -Encoding UTF8
    $json = $null
    try { $json = ($out.Substring($out.IndexOf('{'))) | ConvertFrom-Json } catch { }
    return [pscustomobject]@{ Code = $code; Json = $json; Raw = $out }
}

$Version = (Get-Content (Join-Path $App 'orpheus\VERSIONE.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $Version) { Stop-Install "manca la cartella app accanto a installa.cmd: estrai TUTTO lo zip e rilancia installa.cmd da li'." }
Write-Log "ORPHEUS $Version - installazione in $Root"

if ((Test-Path (Join-Path $CodeDir '.git')) -or ($Legacy -and (Test-Path (Join-Path $Legacy 'orpheus\.git')))) {
    Stop-Install "su questo PC ORPHEUS e' installato con git (PC del responsabile): usa installa_orpheus.cmd, non questo pacchetto."
}

# ---------------------------------------------------------------- 1. Python e codice
Write-Log ''; Write-Log '=== 1. Copia di Python e del codice ==='
$envFile = Join-Path $CodeDir '.env'
$savedEnv = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { $null }
$LocalDir = Join-Path $State 'locale'
$Migrated = $false
$legacyEnv = if ($Legacy) { Join-Path $Legacy 'orpheus\.env' } else { '' }
if (-not $savedEnv -and $legacyEnv -and (Test-Path $legacyEnv)) {
    # Vecchia installazione in C:\ORPHEUS: stesse impostazioni (cartella dei dati, e-mail), cartella locale nuova.
    Write-Log "  Porto impostazioni, registri e copie di sicurezza da $Legacy"
    $savedEnv = [regex]::Replace((Get-Content $legacyEnv -Raw), '(?m)^ORPHEUS_LOCAL_DIR=.*$', "ORPHEUS_LOCAL_DIR=$LocalDir")
    foreach ($sub in @('locale', 'registri')) {
        $from = Join-Path $Legacy $sub
        if (Test-Path $from) {
            New-Item -ItemType Directory -Force -Path (Join-Path $State $sub) | Out-Null
            foreach ($item in @(Get-ChildItem -LiteralPath $from -Force)) {
                Copy-Item -LiteralPath $item.FullName -Destination (Join-Path (Join-Path $State $sub) $item.Name) -Recurse -Force
            }
        }
    }
    $Migrated = $true
}
foreach ($d in @($PyDir, $CodeDir)) {
    if (Test-Path $d) {
        Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue
        if (Test-Path $d) { Stop-Install "non riesco a sostituire $d : chiudi le finestre di ORPHEUS (cruscotto compreso) e rilancia." }
    }
}
Copy-Item -Recurse -Force (Join-Path $App 'python') $PyDir
Copy-Item -Recurse -Force (Join-Path $App 'orpheus') $CodeDir
if ($savedEnv) { Set-Content -Path $envFile -Value $savedEnv -NoNewline -Encoding UTF8 }
$Py = @((Join-Path $PyDir 'python.exe'), (Join-Path $PyDir 'bin/python')) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($env:ORPHEUS_INSTALL_PYTHON) { $Py = $env:ORPHEUS_INSTALL_PYTHON }      # solo per i test su Linux
if (-not $Py) { Stop-Install "Python non trovato in $PyDir dopo la copia." }
$check = & $Py -c "import orpheus, sqlite3; print(orpheus.__version__, sqlite3.sqlite_version)" 2>&1 | Out-String
Write-Log "  Python e ORPHEUS: $($check.Trim())"
if ($LASTEXITCODE -ne 0) { Stop-Install "Python del pacchetto non parte: $($check.Trim())" }

# ---------------------------------------------------------------- 2. configurazione del PC (.env)
Write-Log ''; Write-Log '=== 2. Cartella dei dati ==='
if (-not $savedEnv) {
    $dati = $env:ORPHEUS_INSTALL_DATI
    if (-not $dati) {
        $dati = Read-Host "  Cartella dei dati (database, PDF, schede). Invio = C:\ORPHEUS_DATI"
        if (-not "$dati".Trim()) { $dati = 'C:\ORPHEUS_DATI' }
    }
    $email = $env:ORPHEUS_INSTALL_EMAIL
    if ($null -eq $email) {
        $email = Read-Host "  La tua e-mail (la chiedono Crossref e OpenAlex per le verifiche; resta su questo PC)"
    }
    $email = "$email".Trim()
    $lines = @("ORPHEUS_HOME=$($dati.Trim())", "ORPHEUS_LOCAL_DIR=$LocalDir")
    if ($email -match '^[^@\s]+@[^@\s]+\.[^@\s]+$') { $lines += "ORPHEUS_CONTACT_EMAIL=$email" }
    else { Write-Log "  E-mail non indicata o non valida: le verifiche con Crossref e OpenAlex non partiranno finche' non la aggiungi in $envFile (riga ORPHEUS_CONTACT_EMAIL=...)." }
    Set-Content -Path $envFile -Value ($lines -join "`r`n") -Encoding UTF8
    Write-Log "  Configurazione scritta in $envFile"
}
$home_ = ((Get-Content $envFile | Where-Object { $_ -match '^ORPHEUS_HOME=' } | Select-Object -First 1) -replace '^ORPHEUS_HOME=', '').Trim()
Write-Log "  Dati in: $home_"

# ---------------------------------------------------------------- 3. database
Write-Log ''; Write-Log '=== 3. Database ==='
$dbFile = Join-Path $home_ 'data\orpheus.db'
if (-not (Test-Path $dbFile)) { $dbFile = Join-Path $home_ 'data/orpheus.db' }
if (Test-Path $dbFile) {
    $r = Invoke-Py @('migrate')
    if ($r.Code -ne 0) { Stop-Install "aggiornamento del database non riuscito (dettagli nel registro)." }
    $n = @($r.Json.migrazioni_applicate).Count
    Write-Log ("  Database aggiornato (migrazioni nuove: {0}){1}" -f $n, $(if ($r.Json.backup_prima_della_migrazione) { " - copia di sicurezza: $($r.Json.backup_prima_della_migrazione)" } else { '' }))
} else {
    $r = Invoke-Py @('init')
    if ($r.Code -ne 0) { Stop-Install "creazione del database non riuscita (dettagli nel registro)." }
    Write-Log "  Database nuovo e vuoto creato in $($r.Json.database)"
    New-Item -ItemType Directory -Force -Path (Join-Path $home_ 'inbox\condivisione') | Out-Null
}

# ---------------------------------------------------------------- 4. comandi a doppio clic
Write-Log ''; Write-Log '=== 4. Comandi a doppio clic ==='
$tools = Join-Path $CodeDir 'tools\windows'
if (-not (Test-Path $tools)) { $tools = Join-Path $CodeDir 'tools/windows' }
$copied = 0
foreach ($f in @(Get-ChildItem $tools -File)) { Copy-Item -Force $f.FullName (Join-Path $Root $f.Name); $copied++ }
$pk = Join-Path $CodeDir 'tools\pacchetto'
if (-not (Test-Path $pk)) { $pk = Join-Path $CodeDir 'tools/pacchetto' }
foreach ($f in @('aggiorna.cmd', 'aggiorna.ps1', 'LEGGIMI.txt', 'GUIDA_ORPHEUS.html')) {
    if (Test-Path (Join-Path $pk $f)) { Copy-Item -Force (Join-Path $pk $f) (Join-Path $Root $f); $copied++ }
}
Set-Content -Path (Join-Path $Root 'VERSIONE.txt') -Value $Version -Encoding UTF8
Write-Log "  $copied file copiati in $Root"
$links = Join-Path $Root 'collegamenti_orpheus.ps1'
if (Test-Path $links) { & $links -Root $Root; Write-Log '  Collegamenti: menu Start > ORPHEUS e desktop (ORPHEUS, Comandi di ORPHEUS)' }

# ---------------------------------------------------------------- 5. controllo
Write-Log ''; Write-Log '=== 5. Controllo generale ==='
$r = Invoke-Py @('doctor')
Write-Log ("  orpheus doctor: {0}" -f $(if ($r.Code -eq 0) { 'tutto a posto' } else { "da guardare (codice $($r.Code), dettagli nel registro)" }))

$old = Join-Path $Root 'vecchia_installazione.ps1'
if ($Legacy -and (Test-Path $Legacy) -and (Test-Path $old)) {
    # aggiorna.cmd partito dalla vecchia cartella (le versioni vecchie non dicono da dove: -Da vuoto) resta per ora.
    $fromLegacy = $Aggiornamento -and (-not $Da -or $Da.TrimEnd('\').Equals($Legacy.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase))
    $tieni = if ($fromLegacy) { @('aggiorna.cmd', 'aggiorna.ps1') } else { @() }
    # aggiorna.ps1 nuovo al posto del vecchio (sa trovare la cartella nuova); aggiorna.cmd e' in uso: non si tocca.
    if ($fromLegacy) { Copy-Item -Force (Join-Path $Root 'aggiorna.ps1') (Join-Path $Legacy 'aggiorna.ps1') -ErrorAction SilentlyContinue }
    & $old -Legacy $Legacy -Root $Root -State $State -Tieni $tieni | ForEach-Object { Write-Log "$_" }
}

Write-Log ''
Write-Log "FATTO: ORPHEUS $Version installato in $Root."
if ($Migrated) { Write-Log "  ORPHEUS non sta piu' in ${Legacy}: usa il menu Start > ORPHEUS o 'Comandi di ORPHEUS' sul desktop." }
Write-Log "  Cruscotto: doppio clic su ORPHEUS sul desktop o nel menu Start (o $Root\cruscotto.cmd)."
Write-Log "  Schede di un collega: metti il file .zip in $home_\inbox\condivisione e doppio clic su $Root\importa_schede.cmd."
Write-Log "  Aggiornamenti: doppio clic su $Root\aggiorna.cmd."
Write-Log "  Guida illustrata: $Root\GUIDA_ORPHEUS.html (si apre con il browser)."
Write-Log "Registro: $LogFile"
exit 0
