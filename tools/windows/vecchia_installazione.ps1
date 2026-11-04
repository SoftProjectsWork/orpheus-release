# vecchia_installazione.ps1 - toglie la vecchia installazione in C:\ORPHEUS dopo lo spostamento nelle cartelle nuove
# (2026-11-04, versione 0.76.0). Chiamato alla fine di installa_orpheus.ps1 e di installa.ps1 del pacchetto, solo se
# l'installazione nuova e' riuscita. Toglie solo quello che e' di ORPHEUS; il resto (es. un'esportazione di Tabula)
# resta dov'e', con un file LEGGIMI che spiega dove e' andato ORPHEUS. -Tieni: file da lasciare perche' in uso adesso
# (il .cmd da cui e' partita l'installazione: cmd lo rilegge mentre gira); si tolgono alla prossima installazione.
param([Parameter(Mandatory = $true)][string]$Legacy, [Parameter(Mandatory = $true)][string]$Root,
      [Parameter(Mandatory = $true)][string]$State, [string[]]$Tieni = @())

$ErrorActionPreference = 'Continue'
if (-not (Test-Path -LiteralPath $Legacy)) { exit 0 }
$note = 'LEGGIMI_ORPHEUS_SPOSTATO.txt'
$ours = @(Get-ChildItem -LiteralPath $Legacy -Force | Where-Object {
    ($_.PSIsContainer -and @('orpheus', 'python', 'locale', 'registri') -contains $_.Name.ToLower()) -or
    (-not $_.PSIsContainer -and @('.cmd', '.ps1', '.ico', '.svg', '.txt', '.html', '.md', '.log') -contains $_.Extension.ToLower() -and
     $Tieni -notcontains $_.Name -and $_.Name -ne $note)
})
foreach ($o in $ours) { Remove-Item -LiteralPath $o.FullName -Recurse -Force -ErrorAction SilentlyContinue }
$left = @(Get-ChildItem -LiteralPath $Legacy -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne $note })
if ($left.Count -eq 0) {
    Remove-Item -LiteralPath $Legacy -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "  Vecchia cartella $Legacy tolta."
    exit 0
}
$msg = @("ORPHEUS non sta piu' qui (dal $(Get-Date -Format 'yyyy-MM-dd')).",
         "Programma e comandi a doppio clic: $Root",
         "  (menu Start > ORPHEUS, oppure 'Comandi di ORPHEUS' sul desktop).",
         "Registri e copie di sicurezza: $State",
         'I dati (ORPHEUS_DATI) non sono cambiati.',
         "Quello che resta in questa cartella non e' di ORPHEUS, oppure si toglie alla prossima installazione.")
Set-Content -LiteralPath (Join-Path $Legacy $note) -Value $msg -Encoding UTF8
Write-Output "  In $Legacy resta: $(($left | ForEach-Object { $_.Name }) -join ', ') (vedi $note)"
exit 0
