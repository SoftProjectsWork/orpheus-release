# ORPHEUS - disinstallazione dal PC (doppio clic su disinstalla_orpheus.cmd, o menu Start > ORPHEUS > Disinstalla).
#
# Cosa toglie:
#   - la cartella del programma (%LOCALAPPDATA%\Programs\ORPHEUS): codice, Python o .venv, comandi, file .env del PC;
#   - la cartella di questo PC (%LOCALAPPDATA%\ORPHEUS): registri, stato, log;
#   - una vecchia installazione in C:\ORPHEUS, se c'e' ancora (prima della 0.76.0 tutto stava li');
#   - i collegamenti nel menu Start e sul desktop.
# Cosa NON tocca mai:
#   - la cartella dei dati (ORPHEUS_DATI: database, PDF, schede, manoscritti, revisioni), di solito sul disco esterno;
#   - i repository su GitHub.
# Cosa conserva (spostandolo, non cancellandolo):
#   - le copie di sicurezza del database (locale\backups), se rispondi S (consigliato);
#   - qualsiasi cartella o file in quelle cartelle che non e' di ORPHEUS (es. un'esportazione di Tabula).
#   Finiscono nella cartella dei dati (conservati_disinstallazione_<data>) o, senza disco, in %USERPROFILE%.
#
# Prima di togliere qualcosa: elenco e dimensioni, avviso se il codice ha modifiche non inviate a GitHub, conferma
# scrivendo DISINSTALLA. Con -Prova mostra soltanto cosa farebbe.
# -Root sola (test, vecchie installazioni): tutto in quella cartella, come in C:\ORPHEUS.
param(
    [string]$Root = '',
    [string]$State = '',
    [string]$Conserva = '',
    [switch]$Prova,
    [switch]$Copia          # interno: lo script sta girando da una copia fuori dalle cartelle di ORPHEUS
)
$ErrorActionPreference = 'Stop'
$explicit = [bool]$Root
if (-not $Root) {
    $Root = if ($env:ORPHEUS_INSTALL_ROOT) { $env:ORPHEUS_INSTALL_ROOT }
            elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs\ORPHEUS' } else { 'C:\ORPHEUS' }
}
function Norm([string]$p) { if (-not $p) { return '' }; try { $p = [IO.Path]::GetFullPath($p) } catch { }; return $p.TrimEnd('\', '/') }
$Root = Norm $Root
if (-not $State) {
    $State = if ($env:ORPHEUS_STATE_DIR) { $env:ORPHEUS_STATE_DIR }
             elseif ($env:LOCALAPPDATA -and $Root -eq (Norm (Join-Path $env:LOCALAPPDATA 'Programs\ORPHEUS'))) { Join-Path $env:LOCALAPPDATA 'ORPHEUS' }
             else { $Root }
}
$State = Norm $State
$Legacy = if ($explicit) { '' } else { Norm 'C:\ORPHEUS' }
$targets = @(@($Root, $State, $Legacy) | Where-Object { $_ } | Select-Object -Unique)

function Finish([int]$code) {
    # Aperto dal doppio clic in una finestra sua: la lascio aperta finche' non si legge l'esito.
    if ($Copia) { Read-Host 'Premi Invio per chiudere' | Out-Null }
    exit $code
}
function Say([string]$t, [string]$c = '') { if ($c) { Write-Host $t -ForegroundColor $c } else { Write-Host $t } }
function Size-Of([string]$p) {
    try {
        $b = (Get-ChildItem -LiteralPath $p -Recurse -Force -File -ErrorAction SilentlyContinue |
              Measure-Object -Property Length -Sum).Sum
        if (-not $b) { $b = 0 }
        if ($b -ge 1GB) { return ('{0:N1} GB' -f ($b / 1GB)) }
        return ('{0:N0} MB' -f [math]::Ceiling($b / 1MB))
    } catch { return '?' }
}
function Inside([string]$path, [string]$dir) { return $path -and $dir -and $path.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) }

# Uno script non puo' cancellare la cartella da cui gira: se sono dentro una di quelle, mi copio fuori e riparto.
if (-not $Copia -and @($targets | Where-Object { Inside $PSCommandPath $_ }).Count) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) 'orpheus_disinstalla.ps1'
    Copy-Item -LiteralPath $PSCommandPath -Destination $tmp -Force
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $tmp, '-Copia')
    if ($explicit) { $a += @('-Root', $Root) }
    if ($Conserva) { $a += @('-Conserva', $Conserva) }
    if ($Prova) { $a += '-Prova' }
    & (Get-Process -Id $PID).Path @a
    exit $LASTEXITCODE
}

Say ''
Say '=== ORPHEUS - disinstallazione ===' 'Cyan'
$present = @($targets | Where-Object { Test-Path -LiteralPath $_ })
if ($present.Count -eq 0) {
    Say "Non trovo ${Root}: ORPHEUS non sembra installato su questo PC. Niente da fare."
    Finish 0
}

# ------------------------------------------------------------ cartella dei dati (dal file .env), solo per informare
$DataDir = ''
foreach ($t in $present) {
    $envFile = Join-Path (Join-Path $t 'orpheus') '.env'
    if (-not $DataDir -and (Test-Path -LiteralPath $envFile)) {
        $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*ORPHEUS_HOME\s*=' } | Select-Object -First 1
        if ($line) { $DataDir = ($line -replace '^\s*ORPHEUS_HOME\s*=', '').Trim().Trim('"') }
    }
}
$DataOk = $DataDir -and (Test-Path -LiteralPath $DataDir) -and -not @($present | Where-Object { Inside $DataDir $_ }).Count

# ------------------------------------------------------------ cosa c'e'
$known = @('orpheus', 'python', 'locale', 'registri')
$knownExt = @('.cmd', '.ps1', '.ico', '.svg', '.txt', '.html', '.md', '.lnk', '.log')
$other = @(); $backupDirs = @()
foreach ($t in $present) {
    $other += @(Get-ChildItem -LiteralPath $t -Force | Where-Object {
        if ($_.PSIsContainer) { $known -notcontains $_.Name.ToLower() } else { $knownExt -notcontains $_.Extension.ToLower() }
    })
    $b = Join-Path (Join-Path $t 'locale') 'backups'
    if (@(Get-ChildItem -LiteralPath $b -Force -ErrorAction SilentlyContinue).Count) { $backupDirs += $b }
}
$nBackups = 0; foreach ($b in $backupDirs) { $nBackups += @(Get-ChildItem -LiteralPath $b -Force).Count }

Say ''
Say 'Verra'' TOLTO:'
foreach ($t in $present) { Say "  - $t  ($(Size-Of $t))" }
Say '  - i collegamenti di ORPHEUS nel menu Start e sul desktop'
Say ''
Say 'NON verra'' toccato:'
if ($DataOk) { Say "  - la cartella dei dati $DataDir (database, PDF, schede, manoscritti)" 'Green' }
elseif ($DataDir) { Say "  - la cartella dei dati $DataDir (ora non raggiungibile: disco staccato?)" 'Green' }
else { Say '  - la cartella dei dati ORPHEUS_DATI (dove si trova, non e'' scritto in questo PC)' 'Green' }
Say '  - i repository su GitHub'

# ------------------------------------------------------------ modifiche al codice non inviate a GitHub
$git = (Get-Command git -ErrorAction SilentlyContinue | Select-Object -First 1).Source
foreach ($t in $present) {
    $code = Join-Path $t 'orpheus'
    if (-not $git -or -not (Test-Path -LiteralPath (Join-Path $code '.git'))) { continue }
    $st = (& $git -C $code status --porcelain 2>$null | Out-String).Trim()
    $ahead = (& $git -C $code log --oneline '@{u}..HEAD' 2>$null | Out-String).Trim()
    if ($st -or $ahead) {
        Say ''
        Say "ATTENZIONE: nel codice in $code ci sono modifiche che non sono su GitHub: con la disinstallazione si perdono." 'Yellow'
        if ($st) { Say '  File cambiati:'; ($st -split "`n" | Select-Object -First 10) | ForEach-Object { Say "    $_" } }
        if ($ahead) { Say '  Salvataggi (commit) non inviati:'; ($ahead -split "`n" | Select-Object -First 10) | ForEach-Object { Say "    $_" } }
    }
}

# ------------------------------------------------------------ cosa si conserva
if (-not $Conserva) {
    $stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
    $base = if ($DataOk) { $DataDir } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $Conserva = Join-Path $base ("conservati_disinstallazione_$stamp")
}
if ($other.Count -gt 0) {
    Say ''
    Say "Ci sono anche cose che non sono di ORPHEUS: le SPOSTO (non le cancello) in $Conserva"
    foreach ($o in $other) { Say "  - $($o.FullName)" }
}

if ($Prova) {
    Say ''
    Say "PROVA: non ho tolto niente. Copie di sicurezza del database trovate: $nBackups." 'Cyan'
    Finish 0
}

# ------------------------------------------------------------ domande
$keepBackups = $false
if ($nBackups -gt 0) {
    Say ''
    $r = Read-Host "Ci sono $nBackups copie di sicurezza del database. Le conservo in $Conserva? (S/n)"
    $keepBackups = -not ($r -and $r.Trim().ToUpper().StartsWith('N'))
}
Say ''
$r = Read-Host 'Per disinstallare scrivi DISINSTALLA e premi Invio (qualsiasi altra cosa annulla)'
if (-not $r -or $r.Trim().ToUpper() -ne 'DISINSTALLA') {
    Say 'Annullato: non ho tolto niente.' 'Green'
    Finish 0
}

# ------------------------------------------------------------ chiudo il cruscotto e gli altri comandi di ORPHEUS
try {
    $procs = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $cl = $_.CommandLine
        $_.ProcessId -ne $PID -and $cl -and $cl -notmatch 'orpheus_disinstalla|disinstalla_orpheus' -and
        ($cl -match '-m\s+orpheus' -or @($present | Where-Object { $cl.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count)
    })
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Say "  Chiuso: $($p.Name) ($($p.ProcessId))" } catch { }
    }
    if ($procs.Count) { Start-Sleep -Seconds 2 }
} catch { }   # Get-CimInstance esiste solo su Windows

# ------------------------------------------------------------ sposto quello che si conserva
$moved = @()
$toKeep = @($other | ForEach-Object { $_.FullName })
if ($keepBackups) { $toKeep += $backupDirs }
if ($toKeep.Count) {
    New-Item -ItemType Directory -Force -Path $Conserva | Out-Null
    $i = 0
    foreach ($p in $toKeep) {
        $name = Split-Path $p -Leaf
        if ($backupDirs -contains $p) { $i++; $name = if ($i -gt 1) { "copie_di_sicurezza_database_$i" } else { 'copie_di_sicurezza_database' } }
        elseif (Test-Path -LiteralPath (Join-Path $Conserva $name)) { $name = "${name}_$((Get-Random) % 10000)" }
        try {
            Move-Item -LiteralPath $p -Destination (Join-Path $Conserva $name) -Force
            $moved += $name
        } catch {
            Say "INTERROTTO: non riesco a spostare $p ($($_.Exception.Message)). Non ho cancellato niente." 'Red'
            Finish 1
        }
    }
}

# ------------------------------------------------------------ cancello
$links = $null
foreach ($t in $present) { $c = Join-Path $t 'collegamenti_orpheus.ps1'; if (-not $links -and (Test-Path -LiteralPath $c)) { $links = $c } }
if ($links) { try { & $links -Root $Root -Togli } catch { } }
$desk = ''
try { $desk = [Environment]::GetFolderPath('Desktop') } catch { }
if ($desk) { Remove-Item -LiteralPath (Join-Path $desk 'ORPHEUS.lnk') -Force -ErrorAction SilentlyContinue }   # pacchetti fino alla 0.75
Set-Location ([IO.Path]::GetTempPath())
$robo = Get-Command robocopy -ErrorAction SilentlyContinue
$stuck = @()
foreach ($t in $present) {
    try { Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction Stop } catch { }
    if ((Test-Path -LiteralPath $t) -and $robo) {
        # Percorsi troppo lunghi o file in sola lettura: robocopy con una cartella vuota svuota tutto.
        $empty = Join-Path ([IO.Path]::GetTempPath()) 'orpheus_vuota'
        New-Item -ItemType Directory -Force -Path $empty | Out-Null
        & robocopy $empty $t /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
        Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $empty -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $t) { $stuck += $t }
}

Say ''
if ($stuck.Count) {
    Say "Quasi fatto: alcuni file sono ancora in uso. Riavvia il PC e cancella a mano: $($stuck -join ', ')" 'Yellow'
    $exit = 2
} else {
    Say "ORPHEUS e' stato disinstallato: $($present -join ', ') non ci sono piu'." 'Green'
    $exit = 0
}
if ($moved.Count) { Say "Conservati in ${Conserva}: $($moved -join ', ')" }
if ($DataDir) { Say "I tuoi dati sono ancora in $DataDir. Se non ti servono piu', cancella quella cartella a mano." }
Say 'Per reinstallare: installa_orpheus.cmd (o installa.cmd del pacchetto dei colleghi).'
Finish $exit
