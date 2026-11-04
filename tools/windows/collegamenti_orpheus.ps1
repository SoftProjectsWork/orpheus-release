# collegamenti_orpheus.ps1 - collegamenti di ORPHEUS nel menu Start e sul desktop (2026-11-04).
# Chiamato da installa_orpheus.ps1 e da installa.ps1 del pacchetto (crea), da disinstalla_orpheus.ps1 (-Togli).
#   Menu Start > ORPHEUS: ORPHEUS (cruscotto), Comandi di ORPHEUS (la cartella dei comandi a doppio clic),
#                         Aggiorna ORPHEUS, Disinstalla ORPHEUS
#   Desktop:              ORPHEUS (cruscotto), Comandi di ORPHEUS
# Solo Windows: altrove (test su Linux) non fa niente. Un errore qui non ferma mai l'installazione.
param([Parameter(Mandatory = $true)][string]$Root, [switch]$Togli)

$ErrorActionPreference = 'Continue'
$names = @('ORPHEUS.lnk', 'Comandi di ORPHEUS.lnk')
$desk = ''; $menu = ''
try { $desk = [Environment]::GetFolderPath('Desktop') } catch { }
try { $p = [Environment]::GetFolderPath('Programs'); if ($p) { $menu = Join-Path $p 'ORPHEUS' } } catch { }

if ($Togli) {
    foreach ($n in $names) {
        if ($desk -and (Test-Path -LiteralPath (Join-Path $desk $n))) { Remove-Item -LiteralPath (Join-Path $desk $n) -Force -ErrorAction SilentlyContinue }
    }
    if ($menu -and (Test-Path -LiteralPath $menu)) { Remove-Item -LiteralPath $menu -Recurse -Force -ErrorAction SilentlyContinue }
    exit 0
}

try { $sh = New-Object -ComObject WScript.Shell -ErrorAction Stop } catch { exit 0 }   # non Windows
$icon = Join-Path $Root 'ORPHEUS.ico'
function New-Link([string]$Dir, [string]$Name, [string]$Target, [string]$Desc) {
    if (-not $Dir -or -not (Test-Path -LiteralPath $Target)) { return }
    try {
        New-Item -ItemType Directory -Force -Path $Dir | Out-Null
        $l = $sh.CreateShortcut((Join-Path $Dir $Name))
        $l.TargetPath = $Target
        $l.WorkingDirectory = $Root
        if (Test-Path -LiteralPath $icon) { $l.IconLocation = $icon }
        $l.Description = $Desc
        $l.Save()
    } catch { Write-Host "  (collegamento $Name non creato: $($_.Exception.Message))" }
}
$cruscotto = Join-Path $Root 'cruscotto.cmd'
$update = if (Test-Path -LiteralPath (Join-Path $Root 'installa_orpheus.cmd')) { Join-Path $Root 'installa_orpheus.cmd' } else { Join-Path $Root 'aggiorna.cmd' }
foreach ($d in @($desk, $menu)) {
    New-Link $d 'ORPHEUS.lnk' $cruscotto 'Apre il cruscotto di ORPHEUS nel browser'
    New-Link $d 'Comandi di ORPHEUS.lnk' $Root 'Cartella dei comandi a doppio clic di ORPHEUS'
}
New-Link $menu 'Aggiorna ORPHEUS.lnk' $update 'Scarica e installa la versione nuova di ORPHEUS'
New-Link $menu 'Disinstalla ORPHEUS.lnk' (Join-Path $Root 'disinstalla_orpheus.cmd') 'Toglie ORPHEUS da questo PC (i dati restano)'
exit 0
