# orpheus_comandi.ps1 - i comandi di ORPHEUS a doppio clic (leggi_pdf.cmd, cerca.cmd, controllo.cmd, verifica_doi.cmd,
#                       verifica_pubmed.cmd, benchmark.cmd, doppioni.cmd, autori.cmd, europepmc.cmd, riferimenti.cmd,
#                       verifica_citazioni.cmd, pacchetti.cmd, schede.cmd, controllo_schede.cmd).
#   leggi      legge i PDF nuovi della inbox, sposta in non_completi\ quelli che non sono articoli completi,
#              poi fa il controllo di qualita' (tabella per Excel in rapporti\)
#   cerca      cerca parole nelle pagine dei PDF gia' letti (si possono fare piu' ricerche di seguito)
#   controllo  controllo generale (orpheus doctor)
#   verifica   copia di sicurezza, verifica dei DOI con Crossref (orpheus verify-dois; serve internet; prima rilegge
#              dal testo salvato i DOI non ancora confermati), ricerca dei DOI che il PDF non stampa (orpheus cerca-doi,
#              es. JSCR), poi le regole correnti applicate alle risposte salvate (verify-dois --rivaluta, senza rete)
#              e infine la separazione dei PDF che non sono articoli completi (come in leggi)
#   verifica_pubmed  copia di sicurezza, poi cerca in PubMed gli articoli completi confermati (orpheus verifica-pubmed;
#              serve internet): PMID, MeSH e tipi di pubblicazione solo se il record PubMed ha lo stesso DOI
#   benchmark  file da compilare a mano per il benchmark umano (orpheus benchmark-csv; non modifica il database)
#   doppioni   possibili doppioni e proposta degli autori da Crossref (orpheus doppioni; non modifica il database)
#   autori     scrive gli autori proposti (authors-v1) nel database: copia di sicurezza prima, una transazione,
#              evento di audit (orpheus scrivi-autori); salta gli articoli che hanno gia' autori
#   europepmc  testo integrale aperto in Europe PMC per gli articoli completi (orpheus europepmc --salva, 0.11.0):
#              esito, PMCID e licenza nel database; testo e sezioni solo con licenza Creative Commons; copia prima
#   riferimenti bibliografie degli articoli completi dalle risposte Crossref gia' salvate, senza internet
#              (orpheus riferimenti; solo rapporto, non modifica il database)
#   verifica_citazioni  verifica le bibliografie con Crossref (orpheus verifica-citazioni, regole cite-v2):
#              una riga per riferimento nel database, poi rivaluta senza internet quelle gia' scritte
#              (orpheus verifica-citazioni --rivaluta); copia di sicurezza prima di ogni scrittura
#   pacchetti  pacchetti di lavoro per le schede degli articoli (orpheus pacchetti, T-019): primo lotto (10 testi aperti +
#              20 a sorte), poi lotti successivi di 30 nello stesso ordine del sorteggio (2026-10-16), un file per articolo in rapporti\pacchetti; Claude li legge in sessione e scrive le schede
#   schede     invia le schede scritte da Claude (rapporti\schede\da_inviare): orpheus submit le controlla con i
#              validatori cards-v1; copia di sicurezza prima; accettate -> schede\accettate, rifiutate -> <nome>.motivi.txt
#   controllo_schede  se c'e' un file di controllo compilato: precisione delle schede (orpheus schede-risultati);
#              altrimenti prepara il file con 10 schede a sorte da controllare a mano (orpheus schede-csv)
#   riviste    copia di sicurezza, poi le riviste di riviste_prioritarie.csv nelle tabelle journal/journal_issn, confrontate
#              con le risposte Crossref salvate (titolo, editore, tipo degli ISSN), e gli articoli confermati collegati
#              alla loro rivista (orpheus scrivi-riviste, Q9, regole journals-v2); le righe gia' presenti non cambiano,
#              tranne il tipo 'unknown' di un ISSN quando l'elenco corretto lo indica (journals-v2)
#              poi le versioni datate delle linee guida per gli autori gia' lette (orpheus linee-guida, policy-v1:
#              impronta del testo senza le righe di stampa del browser; nuova versione solo se il testo cambia)
#   schede_riviste  copia di sicurezza, poi le schede delle riviste scritte da Claude (rapporti\schede_riviste\da_inviare;
#              orpheus schede-riviste, journal-cards-v1); poi il file di controllo a mano o, se compilato, la precisione
#   metriche_riviste  copia di sicurezza, poi le metriche ufficiali delle riviste (orpheus metriche-riviste, T-028,
#              metrics-v1): SJR, quartili, h-index dal file di SCImago in inbox\metriche e citazioni medie a 2 anni,
#              h-index e id da OpenAlex (internet); un anno di SCImago non viene mai riscritto
#   semantica  copia di sicurezza, poi i pezzi di testo degli articoli completi (orpheus segmenta, segment-v1) e i loro
#              vettori con il modello multilingue locale (orpheus vettori: la prima volta scarica il modello, ~0,5 GB,
#              nella cartella locale del PC)
#   benchmark_ricerca  recall@5/@10 della ricerca (pagine = prima, parole, significato, ibrida) sulle domande controllate
#              dall'utente (orpheus benchmark-ricerca); solo lettura
#   affermazioni  copia di sicurezza, poi le affermazioni scritte da Claude (rapporti\affermazioni\da_inviare; orpheus
#              affermazioni, claims-v1, livello della prova appraisal-v1); poi il file di controllo a mano o, se
#              compilato, la precisione (orpheus affermazioni-controllo)
#   manoscritto  controllo di un manoscritto Word (.docx) della cartella manoscritti rispetto alle schede delle riviste
#              (orpheus manoscritto, compliance-v1, T-037): sceglie il file, le riviste e il tipo; il nome del file e il
#              testo NON vanno nel registro (R10: solo sul PC), solo numeri ed esiti; il rapporto completo resta in
#              rapporti\manoscritti e si apre nel browser; non modifica il database
#   lettera        lettera di presentazione per la rivista (cover-v1)
#   risposta       risposta ai revisori (response-v1)
#   revisione_sistematica  EURYDIKE E1 (T-051b, review-audit-v1): controllo di una revisione sistematica o meta-analisi
#              Word senza rivista: flusso PRISMA, abstract = flusso, tabella degli studi, k e n, IC, I2, registrazione,
#              strumento RoB, conversioni, checklist PRISMA 2020 e numeri; nel registro solo numeri ed esiti
#   scheda_unica  EURYDIKE E2 (T-051c, cards-v2): copia le schede cards-v1 nella scheda unica dell'articolo (con copia
#              di sicurezza; le cards-v1 non cambiano) ed esporta tutte le schede in rapporti\schede_v2 (JSON + CSV)
#   revisione      revisione simulata del manoscritto per una rivista (T-040 parte 3, review-v1)
#   validazione    validazione della Fase 7 su un manoscritto vero (validation-v1): file di controllo a mano e precisione
#   chat_schede    schede con una chat gratuita (emergenza, ADR-0011): guida passo per passo con gli appunti (copia i
#              messaggi, legge la risposta, copia la correzione); non modifica il database
#   invia_schede_chat  copia di sicurezza, poi le schede accettate della chat nel database (orpheus chat-schede --invia,
#              stessi controlli di submit, esecutore chat_manuale); nel registro solo i conteggi
#   aggiorna_articoli    gruppo di lavora (engine-v1): PDF nuovi, DOI, riviste, PubMed, Europe PMC, bibliografie,
#              autori, pezzi di testo e vettori, report - solo quello che manca, copia di sicurezza prima
#   aggiorna_riviste     gruppo di lavora: elenco delle riviste e linee guida, schede delle riviste scritte da Claude,
#              metriche (SCImago e OpenAlex), report
#   invia_lavoro_claude  gruppo di lavora: schede degli articoli e affermazioni scritte da Claude, pezzi e vettori, report
#   manutenzione   controllo generale, prova di ripristino dell'ultima copia, salute di ORPHEUS, report (solo lettura)
#   prepara_manoscritto  per UN manoscritto e UNA rivista: controllo con la verifica dei riferimenti, poi i pacchetti
#              per Claude della revisione simulata e della lettera, poi il file di validazione (non modifica il database)
#   schede_locali  prova pilota (ADR-0010, T-042): schede dei pacchetti aperti scritte da un modello locale gratuito
#              (Ollama, qwen2.5:7b-instruct; orpheus schede-locali); vanno solo in rapporti\schede\locali, NON nel
#              database: si confrontano a mano con le schede di Claude; il database non viene aperto in scrittura
#   nuovo_articolo  articolo nuovo dai dati dell'utente (orpheus nuovo-articolo, writing-v1, T-041): crea il
#              progetto in nuovi_articoli, controlla studio.csv e risultati.csv, prepara il pacchetto per Claude,
#              controlla le sezioni scritte, apre l'anteprima e registra l'approvazione; nel registro solo gli stati
#   report     pagina HTML di stato in rapporti\report_<data>.html (orpheus report, T-015): documenti, copertura per
#              rivista, verifiche, citazioni, schede; solo lettura, non modifica il database; la apre nel browser
# Tutto viene scritto anche in C:\ORPHEUS\registri\<comando>_<data>.txt; alla fine il registro viene inviato
# anche a GitHub (ramo registri-pc, pubblica_registro.ps1), dove lo legge il loop di Claude nel cloud.
# Questi file sono copiati in C:\ORPHEUS dall'installatore: le versioni aggiornate arrivano con installa_orpheus.cmd.

param([Parameter(Mandatory = $true)][ValidateSet('leggi', 'cerca', 'controllo', 'verifica', 'verifica_pubmed', 'benchmark', 'doppioni', 'autori', 'europepmc', 'riferimenti', 'verifica_citazioni', 'pacchetti', 'schede', 'controllo_schede', 'report', 'prova_ripristino', 'riviste', 'schede_riviste', 'metriche_riviste', 'semantica', 'benchmark_ricerca', 'affermazioni', 'lavora', 'cruscotto', 'manoscritto', 'schede_locali', 'nuovo_articolo', 'chat_schede', 'revisione', 'lettera', 'risposta', 'validazione', 'aggiorna_articoli', 'aggiorna_riviste', 'invia_lavoro_claude', 'manutenzione', 'prepara_manoscritto', 'invia_schede_chat', 'revisione_sistematica', 'scheda_unica', 'esporta_schede', 'importa_schede')][string]$Comando,
      [string]$File = '')

$ErrorActionPreference = 'Continue'
$Root    = if ($env:ORPHEUS_TOOLS_ROOT) { $env:ORPHEUS_TOOLS_ROOT } else { 'C:\ORPHEUS' }   # variabile solo per i test
$CodeDir = Join-Path $Root 'orpheus'
# .venv: installazione con git (PC dell'utente); python\: pacchetto pubblico con Python incluso (PC dei colleghi, T-053)
$VenvPy  = @((Join-Path $CodeDir '.venv\Scripts\python.exe'), (Join-Path $CodeDir '.venv/bin/python'),
             (Join-Path $Root 'python\python.exe')) |
           Where-Object { Test-Path $_ } | Select-Object -First 1
$LogDir  = Join-Path $Root 'registri'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $VenvPy) {
    Write-Host "ORPHEUS non e' installato su questo PC: fai doppio clic su $Root\installa_orpheus.cmd"
    exit 5
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("{0}_{1}.txt" -f $Comando, (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
Set-Content -Path $LogFile -Value "ORPHEUS - $Comando - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - PC $env:COMPUTERNAME" -Encoding UTF8

function Write-Log([string]$Text) {
    Write-Host $Text
    Add-Content -Path $LogFile -Value $Text -Encoding UTF8
}

function Invoke-Orpheus([string[]]$ArgList, [switch]$Live) {
    # Esegue "python -m orpheus ..." e restituisce codice di uscita, JSON letto e testo grezzo (tutto nel registro).
    # Con -Live mostra subito le righe "[avanzamento] ..." che il comando scrive mentre lavora (comandi lunghi).
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $VenvPy
    $quoted = $ArgList | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }
    $psi.Arguments = '-m orpheus ' + ($quoted -join ' ')
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.EnvironmentVariables['PYTHONUTF8'] = '1'
    $proc = [System.Diagnostics.Process]::Start($psi)
    if ($Live) {
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $sb = New-Object System.Text.StringBuilder
        while ($null -ne ($line = $proc.StandardError.ReadLine())) {
            [void]$sb.AppendLine($line)
            if ($line.StartsWith('[avanzamento] ')) { Write-Host ('  ' + (Get-Date -Format 'HH:mm') + '  ' + $line.Substring(14)) }
        }
        $proc.WaitForExit()
        $out = $outTask.Result
        $err = $sb.ToString()
    } else {
        $errTask = $proc.StandardError.ReadToEndAsync()
        $out = $proc.StandardOutput.ReadToEnd()
        $proc.WaitForExit()
        $err = $errTask.Result
    }
    Add-Content -Path $LogFile -Value ("> orpheus " + ($ArgList -join ' ') + "   (codice di uscita $($proc.ExitCode))") -Encoding UTF8
    Add-Content -Path $LogFile -Value $out -Encoding UTF8
    if ($err) { Add-Content -Path $LogFile -Value ("[messaggi]`n" + $err) -Encoding UTF8 }
    $json = $null
    try { $json = $out | ConvertFrom-Json } catch { $json = $null }
    return [pscustomobject]@{ Code = $proc.ExitCode; Json = $json; Raw = $out; Err = $err }
}

function Show-Error($r) {
    if ($r.Json -and $r.Json.errore) { Write-Log "  ERRORE: $($r.Json.errore)" }
    elseif ($r.Err) { Write-Log "  ERRORE: $($r.Err.Trim())" }
    else { Write-Log "  ERRORE (codice $($r.Code)): $($r.Raw.Trim())" }
}

function Format-Counts($obj) {
    if (-not $obj) { return '' }
    return (($obj.PSObject.Properties | ForEach-Object { "$($_.Name) $($_.Value)" }) -join ', ')
}

# ---------------------------------------------------------------- separazione (leggi e verifica)
function Invoke-Separa {
    Write-Log ''
    Write-Log 'SEPARAZIONE: ORPHEUS impara solo dagli articoli completi (abstract di congresso e PDF di 1-2 pagine vanno in non_completi)'
    $sep = Invoke-Orpheus @('separa-non-completi')
    if (-not $sep.Json -or $null -eq $sep.Json.spostati) { Show-Error $sep; return }
    $c = $sep.Json.contenuto_nel_database
    Write-Log ("  Nel database: articoli completi {0}, abstract di congresso {1}, pagine singole {2}" -f `
               $c.articolo_completo, $c.abstract_di_congresso, $c.pagina_singola)
    Write-Log "  Spostati adesso fuori dalla inbox: $($sep.Json.spostati)  (in $($sep.Json.cartella))"
    foreach ($m in @($sep.Json.elenco)) { if ($m) { Write-Log "    - $($m.cartella)\$($m.file)" } }
    foreach ($p in @($sep.Json.problemi)) { if ($p) { Write-Log "    ! $($p.file): $($p.problema)" } }
}

# ---------------------------------------------------------------- leggi
function Invoke-Leggi {
    Write-Log ''
    Write-Log 'LETTURA DEI PDF NUOVI (puo'' richiedere qualche minuto: circa 1 minuto ogni 150 PDF)...'
    $r = Invoke-Orpheus @('ingest-inbox')
    if (-not $r.Json -or -not $r.Json.summary) { Show-Error $r; return }
    $j = $r.Json
    Write-Log ("  Nuovi letti: {0}   Gia' presenti: {1}   Non letti per errore: {2}" -f $j.summary.ingested, $j.summary.duplicate, $j.summary.error)
    if ($j.cartelle_create) { Write-Log ("  Cartelle create per le riviste dell'elenco: " + ($j.cartelle_create -join ', ')) }
    foreach ($e in @($j.errori)) { if ($e) { Write-Log "  ! $($e.path): $($e.error)" } }
    if ($j.ignorati) {
        Write-Log "  File non letti perche' nel posto sbagliato: $(@($j.ignorati).Count)"
        foreach ($i in @($j.ignorati)) { Write-Log "    - $($i.path)"; Write-Log "      $($i.motivo)" }
    }
    if ($j.cartelle_non_in_elenco) { Write-Log "  $($j.cartelle_non_in_elenco.messaggio) ($($j.cartelle_non_in_elenco.sigle -join ', '))" }
    foreach ($p in @($j.problemi_elenco_riviste)) { if ($p) { Write-Log "  Elenco riviste: $p" } }
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
    $nuoveLG = @($j.nuovi | Where-Object { $_ -and ("$($_.file)" -match 'linee_guida') })
    if ($nuoveLG.Count -gt 0) {
        Write-Log "  Linee guida nuove lette: $($nuoveLG.Count). Poi fai doppio clic su riviste.cmd: salva la versione nuova e dice se le schede delle riviste vanno rifatte."
    }

    Invoke-Separa

    Write-Log ''
    Write-Log 'CONTROLLO DI QUALITA'''
    $q = Invoke-Orpheus @('quality-report')
    if (-not $q.Json -or -not $q.Json.riepilogo) { Show-Error $q; return }
    $s = $q.Json.riepilogo
    Write-Log ("  Documenti: {0} ({1} articoli, {2} linee guida), {3} pagine" -f $s.documenti, $s.articoli, $s.linee_guida, $s.pagine)
    $doiFrom = (Format-Counts $s.doi) -replace 'pdf_metadata', 'dai dati del PDF' -replace 'first_page', 'dalla prima pagina' `
               -replace 'ambiguous', 'piu'' DOI in prima pagina' -replace 'none', 'nessuno' `
               -replace 'crossref_search', 'dalla ricerca Crossref'
    Write-Log ("  DOI trovato: {0} articoli su {1} ({2})" -f ($s.articoli - $s.senza_doi), $s.articoli, $doiFrom)
    Write-Log ("  Riviste: " + (Format-Counts $s.riviste))
    if ($s.contenuto) { Write-Log ("  Contenuto: " + (Format-Counts $s.contenuto)) }
    Write-Log ("  Abstract di congresso: {0} - Scansioni (testo da OCR): {1} - Possibili doppioni: {2} - Stesso titolo: {3}" -f `
               $s.abstract_di_congresso, $s.scansioni_ocr, $s.possibili_doppioni, $s.stesso_titolo)
    if ($s.verifica_doi) { Write-Log ("  Verifica dei DOI con Crossref: " + (Format-Counts $s.verifica_doi)) }
    # @( ) intorno: in PowerShell 5.1 un solo risultato non e' un elenco e .Count resterebbe vuoto
    $todo = @(@($q.Json.da_controllare) | Where-Object { $_ })
    if ($todo.Count -gt 0) {
        Write-Log "  Da controllare: $($todo.Count)"
        foreach ($t in $todo) { Write-Log "    - $(Split-Path $t.file -Leaf)"; Write-Log "      $($t.da_controllare)" }
    } else {
        Write-Log '  Niente da controllare.'
    }
    Write-Log "  Tabella completa (si apre con Excel): $($q.Json.file_csv)"
    $open = Read-Host '  Aprire la tabella adesso? Scrivi S e premi Invio (altrimenti solo Invio)'
    if ($open -match '^[sS]' -and (Test-Path $q.Json.file_csv)) { Invoke-Item $q.Json.file_csv }
}

# ---------------------------------------------------------------- cerca
function Invoke-Cerca {
    Write-Log ''
    Write-Log 'RICERCA NEGLI ARTICOLI COMPLETI (per parole e per significato: domande in italiano o in inglese)'
    Write-Log 'Se i pezzi di testo o i vettori non ci sono ancora (semantica.cmd), cerca per parole.'
    while ($true) {
        Write-Host ''
        $text = Read-Host 'Domanda, affermazione o parole da cercare, in italiano o in inglese (solo Invio per uscire)'
        if (-not $text -or -not $text.Trim()) { break }
        Add-Content -Path $LogFile -Value "`nRicerca: $text" -Encoding UTF8
        $words = @($text.Trim() -split '\s+')
        $r = Invoke-Orpheus (@('cerca-testi') + $words + @('--limit', '10'))
        $hits = @()
        if ($r.Json -and $null -ne $r.Json.risultati) { $hits = @(@($r.Json.risultati) | Where-Object { $_ }) }
        if ($hits.Count -gt 0) {
            $modo = if ($r.Json.modo -eq 'ibrida') { 'parole + significato' } else { $r.Json.modo }
            Write-Log "  Articoli trovati ($modo; i piu' pertinenti prima, al massimo 10): $($hits.Count)"
            if ($r.Json.nota) { Write-Log "  ($($r.Json.nota))" }
            $n = 0
            foreach ($h in $hits) {
                $n++
                $doi = if ($h.doi) { "  DOI $($h.doi)" } else { '' }
                Write-Log ("  {0,2}. {1} ({2}){3}" -f $n, $h.titolo, $h.anno, $doi)
                $sez = if ($h.sezione) { ", $($h.sezione)" } else { '' }
                Write-Log ("      pagina {0}{1}: {2}" -f $h.pagine, $sez, (($h.passaggio -replace '\s+', ' ').Trim()))
            }
            continue
        }
        $r = Invoke-Orpheus (@('search') + $words + @('--documents', '--limit', '15'))
        if (-not $r.Json -or $null -eq $r.Json.risultati) { Show-Error $r; continue }
        $hits = @(@($r.Json.risultati) | Where-Object { $_ })
        if ($hits.Count -eq 0) { Write-Log '  Nessun PDF trovato.'; continue }
        Write-Log "  PDF trovati (tutte le parole nella stessa pagina; al massimo 15): $($hits.Count)"
        $n = 0
        foreach ($h in $hits) {
            $n++
            $doi = if ($h.doi_candidate) { "  DOI $($h.doi_candidate)" } else { '' }
            Write-Log ("  {0,2}. {1}{2}" -f $n, (Split-Path $h.original_path -Leaf), $doi)
            Write-Log ("      pagine: {0}  -  dalla pagina {1}: {2}" -f (@($h.pages) -join ', '), $h.best_page,
                       (($h.snippet -replace '\s+', ' ').Trim()))
        }
    }
}

# ---------------------------------------------------------------- verifica
function Invoke-Verifica {
    Write-Log ''
    Write-Log 'COPIA DI SICUREZZA DEL DATABASE (prima della verifica)'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        Write-Log '  Verifica NON eseguita: prima serve una copia di sicurezza riuscita.'
        if ($b.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log "  Fatta: $($b.Json.backup)"
    Write-Log ''
    Write-Log 'VERIFICA DEI DOI CON CROSSREF (serve internet; una richiesta al secondo: circa 3 minuti ogni 150 articoli)...'
    $r = Invoke-Orpheus @('verify-dois')
    if (-not $r.Json -or -not $r.Json.riepilogo) { Show-Error $r; return }
    $j = $r.Json
    $s = $j.riepilogo
    $t = $j.stato_complessivo
    Write-Log ("  Verificati adesso: {0}  (confermati {1}, non trovati {2}, non corrispondono {3})   Gia' verificati prima: {4}" -f `
               $s.verificati_ora, $s.confermato, $s.non_trovato, $s.non_corrisponde, $s.gia_verificati)
    Write-Log ("  Articoli creati nel database: {0}   Richieste a Crossref: {1}" -f $s.articoli_creati, $s.richieste_a_crossref)
    # @( ) intorno: in PowerShell 5.1 un solo elemento non e' un elenco e .Count resterebbe vuoto
    $errs = @(@($j.errori) | Where-Object { $_ })
    if ($errs.Count -gt 0) {
        Write-Log "  Non verificati per un errore: $($errs.Count) (restano da verificare: basta rilanciare verifica_doi.cmd)"
        foreach ($e in $errs) { Write-Log "    ! $($e.file): $($e.errore)" }
    }
    if ($j.interrotto) { Write-Log "  INTERROTTA: $($j.interrotto)" }
    $rilet = @(@($j.doi_riletti) | Where-Object { $_ })
    if ($rilet.Count -gt 0) {
        Write-Log "  DOI riletti dal testo dei PDF (ultime cifre staccate nel PDF): $($rilet.Count)"
        foreach ($x in $rilet) { Write-Log "    - $($x.file): $($x.prima) -> $($x.dopo)" }
    }
    $todo = @(@($j.non_confermati) | Where-Object { $_ })
    if ($todo.Count -gt 0) {
        Write-Log "  Da guardare (nessun articolo creato per questi): $($todo.Count)"
        foreach ($p in $todo) {
            Write-Log "    - $($p.file)   [$($p.esito)]"
            Write-Log "      DOI $($p.doi): $($p.motivo)"
        }
    }
    Write-Log ''
    Write-Log 'RICERCA DEI DOI CHE IL PDF NON STAMPA (Crossref: titolo, volume, fascicolo e pagine stampati nel PDF)...'
    $c = Invoke-Orpheus @('cerca-doi')
    if (-not $c.Json -or -not $c.Json.riepilogo) { Show-Error $c }
    else {
        $cs = $c.Json.riepilogo
        Write-Log ("  Cercati adesso: {0}  (confermati {1}, non corrispondono {2}, non trovati {3}, ambigui {4})   Gia' con DOI: {5}" -f `
                   $cs.cercati, $cs.confermato, $cs.non_corrisponde, $cs.non_trovato, $cs.ambiguo, $cs.gia_con_doi)
        Write-Log ("  Articoli creati nel database: {0}   Richieste a Crossref: {1}" -f $cs.articoli_creati, $cs.richieste_a_crossref)
        foreach ($x in @($c.Json.trovati)) { if ($x) { Write-Log "    + $($x.file): $($x.doi)" } }
        foreach ($x in @($c.Json.non_confermati)) { if ($x) { Write-Log "    - $($x.file)   [$($x.esito)]"; Write-Log "      $($x.motivo)" } }
        foreach ($x in @($c.Json.non_cercabili)) { if ($x) { Write-Log "    ? $($x.file): $($x.motivo)" } }
        foreach ($e in @($c.Json.errori)) { if ($e) { Write-Log "    ! $($e.file): $($e.errore)" } }
        if ($c.Json.interrotto) { Write-Log "  INTERROTTA: $($c.Json.interrotto)" }
    }
    Write-Log ''
    Write-Log 'REGOLE DI CONFRONTO AGGIORNATE: applicate alle risposte gia'' salvate (senza internet)...'
    $v = Invoke-Orpheus @('verify-dois', '--rivaluta')
    if (-not $v.Json -or -not $v.Json.riepilogo) { Show-Error $v }
    else {
        $vs = $v.Json.riepilogo
        $cambiati = [int]$vs.verificati_ora
        Write-Log ("  Esiti ricalcolati: {0}   Invariati: {1}   Articoli creati: {2}" -f $cambiati, $vs.invariati, $vs.articoli_creati)
        $t = $v.Json.stato_complessivo
    }
    Write-Log ''
    Write-Log ("  STATO COMPLESSIVO: {0} PDF con DOI - confermati {1}, non trovati {2}, non corrispondono {3}, da verificare {4}" -f `
               $t.documenti_con_doi, $t.confermato, $t.non_trovato, $t.non_corrisponde, $t.da_verificare)
    Write-Log "  Articoli nel database: $($t.articoli)"
    $acc = if ($v.Json -and $v.Json.accesso) { $v.Json.accesso } else { $j.accesso }
    if ($acc) {
        Write-Log ("  Livello di accesso degli articoli: testo completo {0}, solo abstract {1} (cambiati adesso: {2})" -f `
                   [int]$acc.full_text, [int]$acc.abstract_only, ([int]$j.accesso.cambiati_ora + [int]$v.Json.accesso.cambiati_ora))
    }
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
    Write-Log '  Il dettaglio di ogni confronto (titolo, autori, anno, rivista, pagine) e'' nel registro e nel database.'
    Invoke-Separa
}

# ---------------------------------------------------------------- verifica_pubmed
function Invoke-VerificaPubmed {
    Write-Log ''
    Write-Log 'COPIA DI SICUREZZA DEL DATABASE (prima della ricerca in PubMed)'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        Write-Log '  Ricerca NON eseguita: prima serve una copia di sicurezza riuscita.'
        if ($b.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log "  Fatta: $($b.Json.backup)"
    $p = Invoke-Orpheus @('verifica-pubmed', '--prova')
    if ($p.Json -and $null -ne $p.Json.da_chiedere) {
        Write-Log ("  Articoli completi da cercare: {0}   (circa {1} richieste, {2} min)   Gia' cercati prima: {3}" -f `
                   $p.Json.da_chiedere, $p.Json.richieste_stimate, $p.Json.minuti_stimati, $p.Json.gia_verificati)
    }
    Write-Log ''
    Write-Log 'RICERCA IN PUBMED (NCBI E-utilities, gratuito, senza chiave; serve internet; una richiesta al secondo)...'
    $r = Invoke-Orpheus @('verifica-pubmed')
    if (-not $r.Json -or -not $r.Json.riepilogo) { Show-Error $r; return }
    $j = $r.Json
    $s = $j.riepilogo
    $t = $j.stato_complessivo
    Write-Log ("  Cercati adesso: {0}  (trovati {1}, non trovati {2}, discordanti {3}, ambigui {4})   Errori: {5}   Gia' cercati prima: {6}" -f `
               $s.verificati_ora, $s.trovato, $s.non_trovato, $s.discordante, $s.ambiguo, $s.errori, $s.gia_verificati)
    Write-Log "  Richieste a PubMed: $($s.richieste_a_pubmed)"
    # @( ) intorno: in PowerShell 5.1 un solo elemento non e' un elenco e .Count resterebbe vuoto
    $errs = @(@($j.errori) | Where-Object { $_ })
    if ($errs.Count -gt 0) {
        Write-Log "  Non cercati per un errore: $($errs.Count) (restano da cercare: basta rilanciare verifica_pubmed.cmd)"
        foreach ($e in $errs) { Write-Log "    ! DOI $($e.doi): $($e.errore)" }
    }
    if ($j.interrotto) { Write-Log "  INTERROTTA: $($j.interrotto)" }
    $todo = @(@($j.da_guardare) | Where-Object { $_ })
    if ($todo.Count -gt 0) {
        Write-Log "  Senza PMID (non trovati, discordanti, ambigui): $($todo.Count)"
        foreach ($x in $todo) { Write-Log "    - DOI $($x.doi)   [$($x.esito)]  $($x.motivo)" }
    }
    $warn = @(@($j.avvisi_pubmed) | Where-Object { $_ })
    if ($warn.Count -gt 0) {
        Write-Log "  AVVISI DI PUBMED (ritrattazioni, errata corrige): $($warn.Count) articoli - da guardare, ORPHEUS non cambia nulla da solo"
        foreach ($w in $warn) { Write-Log "    ! DOI $($w.doi) (PMID $($w.pmid)): $(@($w.avvisi) -join '; ')" }
    }
    Write-Log ''
    Write-Log ("  STATO COMPLESSIVO: {0} articoli completi confermati - trovati in PubMed {1}, non trovati {2}, discordanti {3}, ambigui {4}, da cercare {5}" -f `
               $t.articoli_candidati, $t.trovato, $t.non_trovato, $t.discordante, $t.ambiguo, $t.da_verificare)
    Write-Log ("  Articoli con PMID: {0}   Voci MeSH salvate: {1}" -f $t.articoli_con_pmid, $t.voci_mesh)
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
    Write-Log "  $($j.nota_ncbi)"
}

# ---------------------------------------------------------------- benchmark
function Invoke-Benchmark {
    Write-Log ''
    Write-Log 'FILE PER IL BENCHMARK UMANO (articoli completi da controllare a mano sui PDF; il database non viene modificato)'
    $r = Invoke-Orpheus @('benchmark-csv')
    if ($r.Code -ne 0 -or -not $r.Json -or -not $r.Json.file_csv) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $j = $r.Json
    $c = $j.campione
    Write-Log ("  Righe: {0}  (articoli completi: con DOI non confermato {1}, confermati {2}; esclusi perche' non completi: {3})" -f `
               $j.righe, $c.non_confermato, $c.articolo_completo, $j.non_completi_esclusi)
    if ($j.nota) { Write-Log "  Nota: $($j.nota)" }
    if ([int]$j.mai_verificati_esclusi -gt 0) {
        Write-Log "  PDF con DOI mai verificati, esclusi: $($j.mai_verificati_esclusi) (per includerli: prima verifica_doi.cmd, poi di nuovo benchmark.cmd)"
    }
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
    $dati = Split-Path (Split-Path $j.file_csv -Parent) -Parent
    Write-Log ''
    Write-Log "  File da compilare (si apre con Excel): $($j.file_csv)"
    Write-Log '  COME SI COMPILA, una riga alla volta:'
    Write-Log "   1. apri il PDF: cartella $dati\<colonna cartella>, nome nella colonna file, pagina nella colonna pagina"
    Write-Log '   2. confronta il PDF con le colonne crossref_titolo, crossref_autori, crossref_anno, crossref_rivista, crossref_doi'
    Write-Log '   3. nelle colonne titolo_ok, autori_ok, anno_ok, rivista_ok, doi_ok scrivi S (giusto) oppure N (sbagliato)'
    Write-Log '   4. nella colonna note scrivi cosa non va (solo se serve)'
    Write-Log ("   5. alla fine in Excel: File > Salva con nome > tipo 'CSV UTF-8' > nome {0}" -f (Split-Path $j.file_da_salvare -Leaf))
    $open = Read-Host '  Aprire il file adesso? Scrivi S e premi Invio (altrimenti solo Invio)'
    if ($open -match '^[sS]' -and (Test-Path $j.file_csv)) { Invoke-Item $j.file_csv }
}

# ---------------------------------------------------------------- doppioni
function Invoke-Doppioni {
    Write-Log ''
    Write-Log 'POSSIBILI DOPPIONI E AUTORI (solo rapporto: il database non viene modificato)'
    $r = Invoke-Orpheus @('doppioni')
    if ($r.Code -ne 0 -or -not $r.Json -or -not $r.Json.file_csv) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $j = $r.Json
    Write-Log "  Risposte di Crossref lette: $($j.risposte_crossref_lette)  (problemi: $(@($j.problemi).Count))"
    $c = Format-Counts $j.conteggi
    if ($c) { Write-Log "  Coppie trovate: $c" } else { Write-Log '  Nessun possibile doppione.' }
    Write-Log "  Autori (proposta, NON scritta nel database): $(Format-Counts $j.autori_proposta)"
    Write-Log "  Elenco delle coppie (si apre con Excel): $($j.file_csv)"
}

# ---------------------------------------------------------------- autori
function Invoke-Autori {
    Write-Log ''
    Write-Log 'SCRITTURA DEGLI AUTORI NEL DATABASE (proposta authors-v1, da Crossref; prima una copia di sicurezza)'
    $r = Invoke-Orpheus @('scrivi-autori')
    if ($r.Code -ne 0 -or -not $r.Json -or -not $r.Json.nel_database) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $j = $r.Json
    if ($j.backup_prima_della_scrittura) { Write-Log "  Copia di sicurezza: $($j.backup_prima_della_scrittura)" }
    Write-Log "  Scritti ora: $(Format-Counts $j.scritti)"
    Write-Log "  Nel database: autori $($j.nel_database.author) - collegamenti articolo-autore $($j.nel_database.article_author)"
    if ($j.nota) { Write-Log "  $($j.nota)" }
    if ($j.problemi) { Write-Log "  Risposte Crossref non lette: $(@($j.problemi).Count) (dettagli nel registro)" }
}

# ---------------------------------------------------------------- europepmc
function Invoke-EuropePMC {
    Write-Log ''
    Write-Log 'TESTO INTEGRALE APERTO IN EUROPE PMC (salva esito, PMCID e licenze; testi e sezioni solo con licenza Creative Commons)'
    $p = Invoke-Orpheus @('europepmc', '--salva', '--prova')
    if ($p.Code -ne 0 -or -not $p.Json -or $null -eq $p.Json.da_cercare) {
        Show-Error $p
        if ($p.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log ("  Articoli completi da cercare: {0}   (almeno {1} richieste, circa {2} min; serve internet)   Gia' cercati prima: {3}" -f `
               $p.Json.da_cercare, $p.Json.richieste_minime, $p.Json.minuti_stimati, $p.Json.gia_cercati)
    $r = Invoke-Orpheus @('europepmc', '--salva')
    if (-not $r.Json -or -not $r.Json.riepilogo) { Show-Error $r; return }
    $j = $r.Json
    $s = $j.riepilogo
    if ($j.backup_prima_della_scrittura) { Write-Log "  Copia di sicurezza: $($j.backup_prima_della_scrittura)" }
    Write-Log ("  Cercati ora: {0}  (trovati {1}, non trovati {2}, discordanti {3}, ambigui {4})   Errori: {5}   Richieste: {6}" -f `
               $s.cercati, $s.trovati, $s.non_trovati, $s.discordanti, $s.ambigui, $s.errori, $s.richieste_a_europepmc)
    if ($j.salvati) { Write-Log "  Scritti nel database: $(Format-Counts $j.salvati)" }
    $lic = Format-Counts $j.licenze
    if ($lic) { Write-Log "  Licenze dei testi integrali: $lic" }
    $t = $j.stato_complessivo
    if ($t) {
        Write-Log ("  In tutto: trovati {0}, testi aperti {1}, testi salvati {2} ({3} sezioni), articoli con PMCID {4}, con licenza {5}, da cercare {6}" -f `
                   $t.trovato, $t.testo_aperto, $t.testi_salvati, $t.sezioni_salvate, $t.articoli_con_pmcid, $t.articoli_con_licenza, $t.da_cercare)
    }
    $miss = @(@($j.campi_mancanti_nelle_risposte) | Where-Object { $_ })
    if ($miss.Count -gt 0) { Write-Log "  Campi mancanti nelle risposte di Europe PMC: $($miss -join ', ') (Claude lo controlla)" }
    $errs = @(@($j.errori) | Where-Object { $_ })
    if ($errs.Count -gt 0) {
        Write-Log "  Non cercati per un errore: $($errs.Count) (restano da cercare: basta rilanciare europepmc.cmd)"
        foreach ($e in $errs) { Write-Log "    ! DOI $($e.doi): $($e.errore)" }
    }
    if ($j.interrotto) { Write-Log "  INTERROTTA: $($j.interrotto)" }
    Write-Log "  Elenco per articolo (si apre con Excel): $($j.file_csv)"
    Write-Log "  $($j.nota_europepmc)"
}

# ---------------------------------------------------------------- riferimenti
function Invoke-Riferimenti {
    Write-Log ''
    Write-Log 'RIFERIMENTI BIBLIOGRAFICI DA CROSSREF (senza internet; solo rapporto: il database non viene modificato)'
    $r = Invoke-Orpheus @('riferimenti')
    if ($r.Code -ne 0 -or -not $r.Json -or -not $r.Json.riepilogo) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $j = $r.Json
    $s = $j.riepilogo
    Write-Log ("  Articoli completi letti: {0}  (con bibliografia su Crossref {1}; dichiarata ma non disponibile {2})" -f `
               $s.articoli_completi_letti, $s.articoli_con_elenco, $s.articoli_senza_elenco_ma_dichiarati)
    Write-Log ("  Riferimenti: dichiarati {0}, disponibili {1}, con DOI {2}, solo testo {3}" -f `
               $s.riferimenti_dichiarati, $s.riferimenti_depositati, $s.con_doi, $s.solo_testo)
    Write-Log ("  DOI citati diversi: {0} (gia' articoli di ORPHEUS: {1}) - citazioni tra articoli di ORPHEUS: {2}" -f `
               $s.doi_citati_diversi, $s.doi_citati_gia_in_orpheus, $s.citazioni_dentro_il_corpus)
    Write-Log "  Per verificarli tutti con Crossref servirebbero circa $($s.richieste_crossref_per_verificarli) richieste (passo successivo)."
    $who = Format-Counts $j.doi_indicato_da
    if ($who) { Write-Log "  DOI indicati da: $who" }
    $top = @(@($j.piu_citati_nel_corpus) | Where-Object { $_ })
    if ($top.Count -gt 0) {
        Write-Log '  Articoli di ORPHEUS piu'' citati dagli altri:'
        foreach ($t in $top) { Write-Log "    - $($t.doi)  (citato da $($t.citato_da))" }
    }
    $pr = @(@($j.problemi) | Where-Object { $_ })
    if ($pr.Count -gt 0) { Write-Log "  Risposte Crossref non lette: $($pr.Count) (dettagli nel registro)" }
    Write-Log "  Nulla e' stato scritto nel database; nessun riferimento e' ancora verificato."
    Write-Log "  Elenco dei riferimenti (si apre con Excel): $($j.file_csv)"
}

# ---------------------------------------------------------------- verifica_citazioni
function Invoke-VerificaCitazioni {
    Write-Log ''
    Write-Log 'VERIFICA DELLE BIBLIOGRAFIE CON CROSSREF (regole cite-v2: anno, primo autore, titolo; copia di sicurezza prima)'
    $p = Invoke-Orpheus @('verifica-citazioni', '--prova')
    if ($p.Code -ne 0 -or -not $p.Json -or $null -eq $p.Json.articoli_da_verificare) {
        Show-Error $p
        if ($p.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log ("  Articoli da verificare: {0}  (riferimenti {1}; DOI da chiedere a Crossref {2}: almeno {3} richieste, circa {4} min; serve internet)" -f `
               $p.Json.articoli_da_verificare, $p.Json.riferimenti, $p.Json.doi_da_chiedere, $p.Json.richieste_minime, $p.Json.minuti_stimati)
    $r = Invoke-Orpheus @('verifica-citazioni')
    if (-not $r.Json -or -not $r.Json.riepilogo) { Show-Error $r; return }
    $j = $r.Json
    $s = $j.riepilogo
    if ($j.backup_prima_della_scrittura) { Write-Log "  Copia di sicurezza: $($j.backup_prima_della_scrittura)" }
    Write-Log ("  Articoli verificati ora: {0}  - riferimenti scritti {1}: verificati {2}, discordanti {3}, non trovati {4}, non verificati {5}" -f `
               $s.articoli_verificati_ora, $s.riferimenti_scritti, $s.verificato, $s.discordante, $s.non_trovato, $s.non_verificato)
    Write-Log ("  Richieste a Crossref: {0} (gruppi di DOI {1}, DOI chiesti da soli {2})   Errori: {3}   Articoli rimandati: {4}" -f `
               $s.richieste_a_crossref, $s.gruppi, $s.doi_chiesti_da_soli, $s.errori, $s.articoli_rimandati)
    $errs = @(@($j.errori) | Where-Object { $_ })
    foreach ($e in $errs) { Write-Log "    ! $($e.gruppo)$($e.doi): $($e.errore)" }
    if ($j.interrotto) { Write-Log "  INTERROTTA: $($j.interrotto) (rilancia verifica_citazioni.cmd piu' tardi: riprende da dove si e' fermata)" }
    Write-Log ''
    Write-Log 'RIVALUTAZIONE DEI RIFERIMENTI GIA'' SCRITTI (senza internet: risposte Crossref salvate e testi di Europe PMC)'
    $v = Invoke-Orpheus @('verifica-citazioni', '--rivaluta')
    if (-not $v.Json -or $null -eq $v.Json.righe_rilette) { Show-Error $v; return }
    $j = $v.Json
    if ($j.backup_prima_della_scrittura) { Write-Log "  Copia di sicurezza: $($j.backup_prima_della_scrittura)" }
    Write-Log ("  Regole {0}: righe rilette {1}, aggiornate {2}, saltate {3}" -f $j.regole, $j.righe_rilette, $j.righe_aggiornate, $j.righe_saltate)
    $ch = Format-Counts $j.esiti_cambiati
    if ($ch) { Write-Log "  Esiti cambiati: $ch" } else { Write-Log '  Nessun esito cambiato.' }
    $t = $j.stato_complessivo
    Write-Log ("  Nel database: {0} riferimenti di {1} articoli (verificati {2}, discordanti {3}, non trovati {4}, non verificati {5}); verso articoli di ORPHEUS: {6}" -f `
               $t.righe_citation, $t.articoli_con_citazioni, $t.verificato, $t.discordante, $t.non_trovato, $t.non_verificato, $t.citazioni_verso_articoli_di_orpheus)
    $mm = @(@($j.discordanti_esempi) | Where-Object { $_ })
    if ($mm.Count -gt 0) {
        Write-Log '  Esempi di riferimenti discordanti (da guardare):'
        foreach ($m in $mm) { Write-Log "    - $($m.articolo) [rif. $($m.posizione)] $($m.doi_citato): $($m.motivo)" }
    }
}

# ---------------------------------------------------------------- controllo
function Invoke-Controllo {
    Write-Log ''
    Write-Log 'CONTROLLO GENERALE'
    $r = Invoke-Orpheus @('doctor')
    if (-not $r.Json) { Show-Error $r; return }
    $j = $r.Json
    Write-Log "  Cartella dati: $($j.cartelle.ORPHEUS_HOME)"
    Write-Log "  Cartella di questo PC: $($j.cartelle.ORPHEUS_LOCAL_DIR)"
    if ($j.integrita) { Write-Log "  Integrita' del database: $($j.integrita.integrity)  -  Registro delle modifiche valido: $($j.catena_audit_valida)" }
    if ($j.verifica_copia) { Write-Log "  Verifica della copia: $($j.verifica_copia.stato)" }
    if ($j.backup_locali) { Write-Log "  Backup su questo PC: $($j.backup_locali.numero)" }
    if ($null -ne $j.pdf_archivio_mancanti) { Write-Log "  PDF mancanti nell'archivio: $($j.pdf_archivio_mancanti)" }
    if ($j.database -is [string]) { Write-Log "  $($j.database)" }
    foreach ($a in @($j.avvisi_cartelle)) { if ($a) { Write-Log "  AVVISO: $a" } }
    if ($r.Code -eq 0) { Write-Log '  Tutto a posto.' } else { Write-Log "  Il controllo ha segnalato qualcosa (codice $($r.Code)): dettagli nel registro." }
}

# ---------------------------------------------------------------- schede degli articoli (T-019)
function Invoke-Pacchetti {
    Write-Log ''
    Write-Log 'PACCHETTI DI LAVORO PER LE SCHEDE DEGLI ARTICOLI (li legge Claude in sessione; il testo resta su questo PC)'
    # Priorita' (richiesta dell'utente del 2026-10-17 19:13): dal cruscotto arriva gia' scelta, dal doppio clic si chiede.
    $mode = "$env:ORPHEUS_PACCHETTI_MODO"; $sigla = "$env:ORPHEUS_PACCHETTI_RIVISTA"; $n = "$env:ORPHEUS_PACCHETTI_QUANTI"
    if (-not $mode) {
        Write-Host ''
        Write-Host '  Quali articoli preparare?'
        Write-Host '    1) Rivista  - solo gli articoli di una rivista'
        Write-Host '    2) Equo     - lo stesso numero da ogni rivista (se una finisce, le altre la sostituiscono)'
        Write-Host '    3) Casuale  - come adesso, nell''ordine del sorteggio'
        $a = Read-Host '  Scrivi 1, 2 o 3 e premi Invio (solo Invio = 3)'
        $mode = switch ("$a".Trim()) { '1' { 'rivista' } '2' { 'equo' } default { 'casuale' } }
        if ($mode -eq 'rivista') {
            $sig = @()
            try {
                $h = Invoke-Orpheus @('riviste-elenco')
                if ($h.Json -and $h.Json.sigle) { $sig = @($h.Json.sigle) }
            } catch { $sig = @() }
            if ($sig.Count -gt 0) {
                for ($i = 0; $i -lt $sig.Count; $i++) { Write-Host ("    {0}) {1}" -f ($i + 1), $sig[$i]) }
                $k = Read-Host '  Numero della rivista'
                $idx = 0; if ([int]::TryParse("$k", [ref]$idx) -and $idx -ge 1 -and $idx -le $sig.Count) { $sigla = $sig[$idx - 1] }
            }
            if (-not $sigla) { $sigla = Read-Host '  Sigla della rivista (es. JSAMS)' }
        }
        $n = Read-Host '  Quanti pacchetti nuovi? (solo Invio = 10)'
    }
    if (-not "$n".Trim()) { $n = '10' }
    $qty = 0; if (-not [int]::TryParse("$n", [ref]$qty) -or $qty -lt 1) { $qty = 10 }
    Write-Log ("  Priorita': {0}{1}, pacchetti nuovi: {2}" -f $mode, $(if ($mode -eq 'rivista') { " ($sigla)" } else { '' }), $qty)
    $argsP = @('pacchetti', '--modo', $mode, '--quanti', "$qty")
    if ($mode -eq 'rivista') { $argsP += @('--rivista', "$sigla") }
    $r = Invoke-Orpheus $argsP
    if (-not $r.Json -or $null -eq $r.Json.pacchetti_emessi) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $j = $r.Json
    if ($j.priorita) {
        Write-Log ("  Scelti adesso per rivista: {0}" -f (Format-Counts $j.priorita.scelti_per_rivista))
        Write-Log ("  Articoli senza scheda per rivista: {0}; dopo questi ne restano {1}" -f (Format-Counts $j.priorita.per_rivista), $j.priorita.restano_dopo)
    }
    if ($j.lotto -and $null -ne $j.lotto.successivo) {
        Write-Log ("  Lotti successivi: {0} articoli in piu' (ordine fisso del sorteggio, seme {1}), fino a 30 pacchetti aperti; studi originali senza scheda {2}, dopo questi ne restano {3}" -f `
                   $j.lotto.successivo, $j.lotto.seme, $j.lotto.senza_scheda, $j.lotto.restano_dopo)
    }
    if ($j.lotto) {
        Write-Log ("  Primo lotto: {0} testi aperti + {1} articoli a sorte (seme {2}; su {3} studi originali; esclusi: rassegne ecc. {4}, per titolo {5}, a mano {6})" -f `
                   $j.lotto.testi_aperti, $j.lotto.a_sorte, $j.lotto.seme, $j.lotto.popolazione_a_sorte, `
                   $j.lotto.esclusi.tipo_di_pubblicazione, $j.lotto.esclusi.titolo, $j.lotto.esclusi.a_mano)
    }
    Write-Log ("  Pacchetti pronti: {0}  (nuovi adesso: {1})  -  Articoli con scheda gia' accettata: {2}" -f `
               $j.pacchetti_emessi, $j.esecuzioni_nuove, $j.gia_con_scheda)
    foreach ($p in @($j.problemi)) { if ($p) { Write-Log "  ! $($p.article_id): $($p.problema)" } }
    Write-Log "  Cartella dei pacchetti: $($j.cartella_pacchetti)"
    Write-Log "  Claude scrivera' le schede in: $($j.cartella_schede_da_inviare)"
    Write-Log ("  Stato: schede accettate {0}, pacchetti aperti {1}" -f $j.stato.schede_accettate, $j.stato.pacchetti_aperti)
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
    # Decisione dell'utente del 2026-10-12: i pacchetti con testo aperto (licenza Creative Commons) vanno su GitHub,
    # ramo pacchetti-cc, cosi' il loop orario scrive quelle schede anche a PC spento. Gli altri restano su questo PC.
    $cc = @(@($j.pacchetti_testo_aperto_cc) | Where-Object { $_ })
    $Pub = Join-Path $Root 'pubblica_registro.ps1'
    if ($cc.Count -gt 0 -and (Test-Path $Pub)) {
        Write-Log "  Pacchetti con testo aperto (licenza CC) da mandare al loop di Claude: $($cc.Count)"
        & $Pub -File $cc -Branch 'pacchetti-cc' -Folder 'pacchetti' 6>&1 | ForEach-Object { Write-Log "$_" }
    }
    Write-Log '  Gli altri pacchetti li legge Claude nelle sessioni con il PC collegato.'
}

function Invoke-Schede {
    Write-Log ''
    Write-Log 'INVIO DELLE SCHEDE SCRITTE DA CLAUDE (controlli cards-v1; prima una copia di sicurezza)'
    $r = Invoke-Orpheus @('submit', '--dal-cloud')
    if (-not $r.Json -or $null -eq $r.Json.schede_trovate) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $j = $r.Json
    $dl = $j.schede_dal_loop
    if ($dl) {
        Write-Log ("  Schede scritte dal loop di Claude (GitHub) copiate adesso: {0}" -f @(@($dl.copiate) | Where-Object { $_ }).Count)
        if ($dl.problema) { Write-Log "  ($($dl.problema))" }
    }
    if ($j.nota) { Write-Log "  $($j.nota)"; return }
    if ($j.backup_prima_della_scrittura) { Write-Log "  Copia di sicurezza: $($j.backup_prima_della_scrittura)" }
    Write-Log ("  Schede trovate: {0}  -  accettate {1}, rifiutate {2}, gia' accettate {3}" -f `
               $j.schede_trovate, $j.accettate, $j.rifiutate, $j.gia_accettate)
    foreach ($s in @($j.schede)) {
        if (-not $s) { continue }
        Write-Log "    - $($s.file): $($s.esito)"
        foreach ($m in @($s.motivi | Select-Object -First 5)) { if ($m) { Write-Log "        $m" } }
    }
    Write-Log ("  Nel database: schede {0}, valori {1}; pacchetti ancora aperti {2}" -f $j.stato.schede_accettate, $j.stato.valori, $j.stato.pacchetti_aperti)
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
    if ([int]$j.rifiutate -gt 0) { Write-Log '  Scrivi a Claude: "schede inviate" (correggera'' quelle rifiutate leggendo i file .motivi.txt).' }
    else { Write-Log '  Scrivi a Claude: "schede inviate".' }
}

function Invoke-ControlloSchede {
    Write-Log ''
    Write-Log 'CONTROLLO A MANO DELLE SCHEDE (il database non viene modificato)'
    $res = Invoke-Orpheus @('schede-risultati')
    if ($res.Code -eq 0 -and $res.Json -and $res.Json.precisione_complessiva) {
        $p = $res.Json.precisione_complessiva
        Write-Log "  File compilato: $($res.Json.file)"
        Write-Log ("  Valori giusti {0}, sbagliati {1}, non compilati {2}  -  precisione {3}" -f $p.giusti, $p.sbagliati, $p.non_compilati, $p.precisione)
        Write-Log ("  Schede tutte giuste: {0} su {1}" -f $res.Json.schede_tutte_giuste.tutte_giuste, $res.Json.schede_tutte_giuste.compilate)
        Write-Log '  Scrivi a Claude: "controllo schede fatto".'
        return
    }
    $r = Invoke-Orpheus @('schede-csv')
    if ($r.Code -ne 0 -or -not $r.Json -or -not $r.Json.file_csv) { Show-Error $r; return }
    $j = $r.Json
    Write-Log ("  Schede nel campione: {0}  -  righe da controllare: {1}" -f $j.schede, $j.righe)
    if ($j.nota) { Write-Log "  Nota: $($j.nota)" }
    $dati = Split-Path (Split-Path $j.file_csv -Parent) -Parent
    Write-Log "  File da compilare (si apre con Excel): $($j.file_csv)"
    Write-Log '  COME SI COMPILA, una riga alla volta:'
    Write-Log "   1. apri il PDF: cartella $dati\<colonna cartella>, nome nella colonna file, pagina nella colonna pagina"
    Write-Log '   2. guarda campo, valore, unita e gruppo, e la colonna cosa_controllare'
    Write-Log '   3. nella colonna giusto scrivi S (giusto) oppure N (sbagliato); nella colonna note cosa non va (solo se serve)'
    Write-Log ("   4. alla fine in Excel: File > Salva con nome > tipo 'CSV UTF-8' > nome {0}" -f (Split-Path $j.file_da_salvare -Leaf))
    Write-Log '   5. poi rilancia controllo_schede.cmd: calcola la precisione'
    $open = Read-Host '  Aprire il file adesso? Scrivi S e premi Invio (altrimenti solo Invio)'
    if ($open -match '^[sS]' -and (Test-Path $j.file_csv)) { Invoke-Item $j.file_csv }
}

# ---------------------------------------------------------------- report
function Invoke-Report {
    Write-Log ''
    Write-Log 'REPORT DI STATO (solo lettura: il database non viene modificato)'
    $r = Invoke-Orpheus @('report')
    if ($r.Code -ne 0 -or -not $r.Json -or -not $r.Json.file_html) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $j = $r.Json
    Write-Log ("  Articoli: {0}  - PDF letti: {1}" -f $j.articoli, $j.pdf)
    foreach ($v in @(@($j.riviste) | Where-Object { $_ })) {
        Write-Log ("  - {0}: articoli {1}, DOI confermati {2}, in PubMed {3}, testi aperti {4}, schede {5}" -f `
                   $v.rivista, $v.articoli, $v.doi_confermato, $v.pubmed, $v.testo_aperto, $v.scheda)
    }
    Write-Log ("  Riferimenti: {0} (verificati {1}) - Schede: {2} ({3} valori)" -f `
               $j.riferimenti, $j.riferimenti_verificati, $j.schede, $j.valori)
    Write-Log ("  Riviste: schede delle riviste {0}, metriche {1}" -f $j.schede_riviste, $j.metriche_riviste)
    $av = @($j.avvisi_linee_guida | Where-Object { $_ })
    if ($av.Count -eq 0) { Write-Log '  Avvisi sulle linee guida: nessuno' }
    foreach ($x in $av) { Write-Log "  ATTENZIONE: $x" }
    Write-Log "  Pagina del report: $($j.file_html)"
    if (Test-Path $j.file_html) { Invoke-Item $j.file_html }
}

# ---------------------------------------------------------------- prova di ripristino
function Invoke-ProvaRipristino {
    Write-Log ''
    Write-Log 'PROVA DI RIPRISTINO (il database non viene modificato): copia di sicurezza nuova, poi ripristino in una cartella temporanea e controlli'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        if ($b.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log "  Copia di sicurezza: $($b.Json.backup)"
    $r = Invoke-Orpheus @('prova-ripristino')
    if (-not $r.Json -or -not $r.Json.esito) { Show-Error $r; return }
    $j = $r.Json
    foreach ($p in $j.controlli.PSObject.Properties) {
        $mark = if ($p.Value.ok) { 'ok' } else { 'NO' }
        Write-Log ("  - {0}: {1}" -f $p.Name, $mark)
    }
    foreach ($p in $j.conteggi.PSObject.Properties) {
        Write-Log ("    {0}: backup {1}, database {2}" -f $p.Name, $p.Value.backup, $p.Value.attuale)
    }
    if ($j.esito -eq 'riuscita') { Write-Log "  ESITO: RIUSCITA - il backup $($j.backup) si ripristina correttamente." }
    else { Write-Log "  ESITO: FALLITA - il backup $($j.backup) non supera tutti i controlli (vedi sopra). Il database non e' stato toccato." }
}

# ---------------------------------------------------------------- riviste e linee guida (Q9, Fase 2)
function Invoke-Riviste {
    Write-Log ''
    Write-Log 'COPIA DI SICUREZZA DEL DATABASE (prima di scrivere le riviste)'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        Write-Log '  Riviste NON scritte: prima serve una copia di sicurezza riuscita.'
        if ($b.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log "  Fatta: $($b.Json.backup)"
    Write-Log ''
    Write-Log 'RIVISTE DELL''ELENCO NEL DATABASE (tabelle journal e journal_issn; confronto con le risposte Crossref salvate)...'
    $r = Invoke-Orpheus @('scrivi-riviste')
    if (-not $r.Json -or -not $r.Json.scritto) { Show-Error $r; return }
    $j = $r.Json
    foreach ($x in @($j.riviste)) {
        if (-not $x) { continue }
        Write-Log ("  {0}: {1}  [{2}]  articoli confermati {3}" -f $x.sigla, $x.titolo, $x.azione, $x.articoli)
        $issn = (@($x.issn) | Where-Object { $_ } | ForEach-Object { "$($_.issn) $($_.tipo) ($($_.fonte))" }) -join ', '
        if ($issn) { Write-Log "      ISSN: $issn" }
        if ($x.editore) { Write-Log "      Editore: $($x.editore)   Stato: $($x.stato)" }
        foreach ($p in @($x.problemi)) { if ($p) { Write-Log "      ! $p" } }
        foreach ($n in @($x.note)) { if ($n) { Write-Log "      - $n" } }
    }
    foreach ($p in @($j.problemi_elenco_riviste)) { if ($p) { Write-Log "  Elenco riviste: $p" } }
    $w = $j.scritto
    Write-Log ''
    Write-Log ("  Scritte adesso: riviste {0}, articoli collegati {1}, PDF collegati {2}" -f $w.riviste_scritte, $w.articoli_collegati, $w.documenti_collegati)
    if ([int]$w.tipi_issn_completati -gt 0) { Write-Log "  Tipi degli ISSN completati (erano sconosciuti): $($w.tipi_issn_completati)" }
    if ([int]$j.articoli_senza_rivista_in_elenco -gt 0) {
        Write-Log "  Articoli confermati di riviste che non sono nell'elenco: $($j.articoli_senza_rivista_in_elenco) (non collegati)"
    }
    $d = $j.nel_database
    Write-Log ("  Nel database: riviste {0}, ISSN {1}, articoli con rivista {2} su {3}" -f $d.journal, $d.journal_issn, $d.articoli_con_rivista, $d.articoli)
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
    Write-Log ''
    Write-Log 'LINEE GUIDA PER GLI AUTORI: VERSIONI DATATE (tabella journal_policy_version; nuova versione solo se il testo cambia)...'
    $g = Invoke-Orpheus @('linee-guida')
    if (-not $g.Json -or -not $g.Json.scritto) { Show-Error $g; return }
    foreach ($x in @($g.Json.linee_guida)) {
        if (-not $x) { continue }
        Write-Log ("  {0}: {1}  [{2}]  pagine {3}, impronta {4}" -f $x.sigla, $x.file, $x.azione, $x.pagine, $x.impronta)
        if ($x.stampa_come_nel_pdf) { Write-Log "      Stampata dal browser: $($x.stampa_come_nel_pdf)" }
        if ($x.indirizzo) {
            $u = if ($x.indirizzo_uguale_all_elenco -eq $true) { ' (indirizzo dell''elenco)' } elseif ($x.indirizzo_uguale_all_elenco -eq $false) { ' (DIVERSO dall''elenco)' } else { '' }
            Write-Log "      Indirizzo: $($x.indirizzo)$u"
        }
        if ($x.sostituisce_una_versione) { Write-Log '      ! Testo cambiato rispetto alla versione precedente: da rileggere' }
        if ($x.motivo) { Write-Log "      - $($x.motivo)" }
    }
    $gw = $g.Json.scritto; $gd = $g.Json.nel_database
    Write-Log ''
    Write-Log ("  Scritte adesso: versioni {0}, linee guida collegate alla rivista {1}" -f $gw.versioni_scritte, $gw.linee_guida_collegate)
    Write-Log ("  Nel database: versioni {0} di {1} riviste (da rileggere {2}); linee guida lette {3}" -f $gd.versioni, $gd.riviste_con_linee_guida, $gd.da_rileggere, $gd.linee_guida_lette)
    Write-Log ''
    Write-Log 'AVVISI: LINEE GUIDA CAMBIATE DOPO LA SCHEDA DELLA RIVISTA (confronto frase per frase, policy-diff-v1)...'
    $av = @($g.Json.avvisi | Where-Object { $_ })
    if ($av.Count -eq 0) { Write-Log '  Nessun avviso: le schede delle riviste sono sulle linee guida piu'' recenti.' }
    foreach ($a in $av) {
        Write-Log ("  ATTENZIONE: {0} - {1}: linee guida del {2} ({3}) invece di quelle del {4} ({5})" -f $a.rivista, $a.avviso, $a.versione_nuova.giorno, $a.versione_nuova.file, $a.versione_precedente.giorno, $a.versione_precedente.file)
        Write-Log ("      Frasi tolte {0}, aggiunte {1} (testo uguale al {2:P0})" -f $a.frasi_tolte, $a.frasi_aggiunte, [double]$a.testo_uguale)
        foreach ($e in @($a.esempi)) { if ($e) { Write-Log ("      {0} p.{1}: {2}" -f $e.tipo, $e.pagina, $e.frase) } }
        if ($null -ne $a.valori_della_scheda) {
            Write-Log ("      Valori della scheda da ricontrollare (la frase citata non c'e' piu'): {0} su {1}" -f @($a.valori_da_ricontrollare).Count, $a.valori_della_scheda)
            foreach ($v in @($a.valori_da_ricontrollare | Select-Object -First 15)) { if ($v) { Write-Log ("        - {0} {1}: {2}" -f $v.campo, $v.tipo_articolo, $v.valore) } }
            Write-Log '      La scheda va riscritta da Claude sulle linee guida nuove: scrivi a Claude "linee guida cambiate".'
        }
    }
}

# ---------------------------------------------------------------- schede delle riviste (T-027, Fase 2)
function Invoke-SchedeRiviste {
    Write-Log ''
    Write-Log 'COPIA DI SICUREZZA DEL DATABASE (prima di scrivere le schede delle riviste)'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        Write-Log '  Schede NON inviate: prima serve una copia di sicurezza riuscita.'
        if ($b.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log "  Fatta: $($b.Json.backup)"
    Write-Log ''
    Write-Log 'SCHEDE DELLE RIVISTE SCRITTE DA CLAUDE (journal-cards-v1: ogni valore con pagina e frase delle linee guida)...'
    $r = Invoke-Orpheus @('schede-riviste')
    if (-not $r.Json -or $null -eq $r.Json.schede_trovate) { Show-Error $r; return }
    $j = $r.Json
    Write-Log ("  Schede trovate: {0}  -  accettate {1}, rifiutate {2}, non inviate {3}" -f $j.schede_trovate, $j.accettate, $j.rifiutate, $j.non_inviate)
    foreach ($s in @($j.schede)) {
        if (-not $s) { continue }
        $n = if ($s.valori) { " ($($s.valori) valori)" } else { '' }
        Write-Log "    - $($s.file): $($s.esito)$n"
        if ($s.motivo) { Write-Log "        $($s.motivo)" }
        foreach ($m in @($s.motivi | Select-Object -First 5)) { if ($m) { Write-Log "        $m" } }
    }
    Write-Log ("  Nel database: schede {0} di {1} riviste, valori {2}; da rifare (linee guida cambiate) {3}" -f $j.stato.schede, $j.stato.riviste, $j.stato.valori, $j.stato.schede_da_rifare)
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)"; return }
    Write-Log ''
    Write-Log 'CONTROLLO A MANO DI TUTTE LE SCHEDE DELLE RIVISTE...'
    $c = Invoke-Orpheus @('schede-riviste-controllo')
    if (-not $c.Json -or -not $c.Json.modo) { Show-Error $c; return }
    $k = $c.Json
    if ($k.modo -eq 'file da compilare') {
        Write-Log "  File da compilare ($($k.righe) righe, $($k.schede) schede): $($k.file_csv)"
        Write-Log '  Aprilo con Excel: per ogni riga guarda la pagina indicata delle linee guida e scrivi S o N nella colonna giusto.'
        Write-Log "  Salvalo come: $($k.file_da_salvare)  (CSV), poi rilancia schede_riviste.cmd."
    } elseif ($k.modo -eq 'risultati') {
        $pc = $k.precisione_complessiva
        Write-Log ("  Valori controllati: giusti {0}, sbagliati {1}, non compilati {2}; precisione {3}" -f $pc.giusti, $pc.sbagliati, $pc.non_compilati, $pc.precisione)
        foreach ($x in @($k.sbagliati | Select-Object -First 15)) { if ($x) { Write-Log "    ! riga $($x.n) $($x.rivista) - $($x.campo) $($x.tipo_articolo): $($x.valore) $($x.note)" } }
        Write-Log "  Giudizi registrati nel database: $($k.valori_registrati)"
    } else { Write-Log "  $($k.motivo)" }
    Write-Log '  Scrivi a Claude: "schede delle riviste inviate".'
}

# ---------------------------------------------------------------- metriche delle riviste (T-028, Fase 2)
function Invoke-MetricheRiviste {
    Write-Log ''
    Write-Log 'COPIA DI SICUREZZA DEL DATABASE (prima di scrivere le metriche delle riviste)'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        Write-Log '  Metriche NON scritte: prima serve una copia di sicurezza riuscita.'
        if ($b.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log "  Fatta: $($b.Json.backup)"
    Write-Log ''
    Write-Log 'METRICHE DELLE RIVISTE (SCImago: file in inbox\metriche; OpenAlex: internet, una richiesta al secondo)...'
    $r = Invoke-Orpheus @('metriche-riviste')
    if (-not $r.Json -or -not $r.Json.scritto) { Show-Error $r; return }
    $j = $r.Json
    if (@($j.file_scimago).Count -eq 0) { Write-Log "  Nessun file di SCImago in $($j.cartella_scimago)" }
    foreach ($f in @($j.file_scimago)) { if ($f) { Write-Log ("  File SCImago: {0} (anno {1}, {2} righe)" -f $f.file, $f.anno, $f.righe) } }
    foreach ($x in @($j.scimago) + @($j.openalex)) {
        if (-not $x) { continue }
        $tit = if ($x.titolo_scimago) { " ($($x.titolo_scimago))" } elseif ($x.titolo_openalex) { " ($($x.titolo_openalex), $($x.openalex_id))" } else { '' }
        Write-Log ("  {0} - {1} {2}: {3}{4}  nuove {5}, uguali {6}" -f $x.titolo, $x.fonte, $x.anno, $x.esito, $tit, $x.nuove, $x.uguali)
        if ([int]$x.nuove -gt 0) { Write-Log ("      " + ((@($x.metriche) | Where-Object { $_ }) -join '; ')) }
        foreach ($d in @($x.diverse)) { if ($d) { Write-Log "      ~ $d" } }
        foreach ($p in @($x.problemi)) { if ($p) { Write-Log "      ! $p" } }
    }
    foreach ($p in @($j.problemi)) { if ($p) { Write-Log "  ! $p" } }
    $w = $j.scritto
    Write-Log ''
    Write-Log ("  Scritte adesso: metriche {0}, aggiornate {1} (OpenAlex), id OpenAlex {2}; richieste a OpenAlex {3}" -f $w.metriche_scritte, $w.metriche_aggiornate, $w.id_openalex_scritti, $j.richieste_openalex)
    $d = $j.nel_database
    Write-Log ("  Nel database: metriche {0}, riviste con metriche {1} su {2}, con id OpenAlex {3}" -f $d.metriche, $d.riviste_con_metriche, $d.riviste, $d.riviste_con_id_openalex)
    Write-Log ''
    Write-Log '  Rivista: SJR | quartile | h-index SCImago | citazioni per documento 2 anni (SCImago) | citazioni medie 2 anni (OpenAlex) | h-index OpenAlex'
    foreach ($x in @($j.tabella)) {
        if (-not $x) { continue }
        Write-Log ("  {0}: {1} | {2} | {3} | {4} | {5} | {6}" -f $x.titolo, $x.sjr, $x.quartile, $x.h_index_scimago, $x.citazioni_2_anni_scimago, $x.citazioni_2_anni_openalex, $x.h_index_openalex)
        if ($x.categorie) { Write-Log ("      " + (@($x.categorie) -join '; ')) }
    }
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
    Write-Log '  Scrivi a Claude: "metriche fatte".'
}

# ---------------------------------------------------------------- ricerca per significato (Fase 3, T-030/T-031)
function Invoke-Semantica {
    Write-Log ''
    Write-Log 'COPIA DI SICUREZZA DEL DATABASE (prima di scrivere pezzi di testo e vettori)'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        Write-Log '  Niente scritto: prima serve una copia di sicurezza riuscita.'
        if ($b.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log "  Fatta: $($b.Json.backup)"
    Write-Log ''
    Write-Log 'PEZZI DI TESTO DEGLI ARTICOLI COMPLETI (segment-v1: circa un paragrafo, con pagina e sezione; senza bibliografia)...'
    $s = Invoke-Orpheus @('segmenta')
    if (-not $s.Json -or -not $s.Json.scritto) { Show-Error $s; return }
    $sr = $s.Json.riepilogo; $sw = $s.Json.scritto
    Write-Log ("  Articoli {0}, pezzi {1} (parole: mediana {2}, da {3} a {4}); articoli senza pezzi {5}" -f $sr.articoli, $sr.segmenti, $sr.parole_mediana, $sr.parole_min, $sr.parole_max, $sr.articoli_senza_segmenti)
    Write-Log ("  Scritti adesso: articoli {0}, pezzi {1} (sostituiti {2})" -f $sw.articoli_cambiati, $sw.segmenti_scritti, $sw.segmenti_sostituiti)
    Write-Log ("  Sezioni piu' frequenti: " + (Format-Counts $sr.sezioni_principali))
    Write-Log ''
    Write-Log 'VETTORI CON IL MODELLO MULTILINGUE LOCALE (intfloat/multilingual-e5-small)...'
    Write-Log '  La prima volta scarica il modello (circa 0,5 GB) e calcola tutti i vettori: puo'' richiedere 10-30 minuti. Non chiudere la finestra.'
    $v = Invoke-Orpheus @('vettori')
    if (-not $v.Json -or -not $v.Json.scritto) { Show-Error $v; return }
    if ($v.Json.scaricato) { Write-Log "  Modello scaricato in $($v.Json.cartella_modello)" }
    $vd = $v.Json.nel_database
    Write-Log ("  Vettori calcolati adesso: {0}; nel database {1} pezzi su {2} hanno il vettore" -f $v.Json.scritto.scritti, $vd.con_vettore, $vd.segmenti)
    Write-Log '  Ora cerca.cmd cerca anche per significato (domande in italiano o in inglese).'
    Write-Log '  Scrivi a Claude: "semantica fatta".'
}

function Invoke-BenchmarkRicerca {
    Write-Log ''
    Write-Log 'BENCHMARK DELLA RICERCA (domande controllate da te; solo lettura)...'
    $r = Invoke-Orpheus @('benchmark-ricerca')
    if (-not $r.Json -or -not $r.Json.riepilogo) { Show-Error $r; return }
    $j = $r.Json
    Write-Log ("  File: {0}  -  domande misurate {1} (scartate: {2})" -f $j.file, $j.domande, (Format-Counts $j.scartate))
    Write-Log '  Metodo_lingua: recall@5 | recall@10 | almeno uno nei primi 10 | MRR'
    foreach ($p in $j.riepilogo.PSObject.Properties) {
        $x = $p.Value
        Write-Log ("  {0}: {1} | {2} | {3} | {4}" -f $p.Name, $x.'recall@5', $x.'recall@10', $x.'hit@10', $x.mrr)
    }
    Write-Log "  Posizioni per domanda: $($j.file_risultati)"
    Write-Log '  Scrivi a Claude: "benchmark fatto".'
}

# ---------------------------------------------------------------- affermazioni ed evidenze (Fase 3, T-033)
function Invoke-Affermazioni {
    Write-Log ''
    Write-Log 'COPIA DI SICUREZZA DEL DATABASE (prima di scrivere le affermazioni)'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        Write-Log '  Affermazioni NON inviate: prima serve una copia di sicurezza riuscita.'
        if ($b.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    Write-Log "  Fatta: $($b.Json.backup)"
    Write-Log ''
    Write-Log 'AFFERMAZIONI SCRITTE DA CLAUDE (claims-v1: ogni affermazione con pagina e frase del PDF; livello della prova appraisal-v1)...'
    $r = Invoke-Orpheus @('affermazioni')
    if (-not $r.Json -or $null -eq $r.Json.file_trovati) { Show-Error $r; return }
    $j = $r.Json
    Write-Log ("  File trovati: {0}  -  accettati {1}, rifiutati {2}, non inviati {3}" -f $j.file_trovati, $j.accettati, $j.rifiutati, $j.non_inviati)
    foreach ($f in @($j.file)) {
        if (-not $f) { continue }
        $lng = if ($f.lingua -eq 'en') { 'inglese' } else { 'italiano' }
        $n = if ($f.affermazioni) { " ($($f.affermazioni) affermazioni in $lng, livello $($f.livello))" } else { '' }
        Write-Log "    - $($f.file): $($f.esito)$n"
        if ($f.motivo) { Write-Log "        $($f.motivo)" }
        foreach ($m in @($f.motivi | Select-Object -First 5)) { if ($m) { Write-Log "        $m" } }
    }
    $s = $j.stato
    Write-Log ("  Nel database: affermazioni {0}, evidenze {1}, articoli {2}; articoli per livello: {3}" -f $s.affermazioni, $s.evidenze, $s.articoli, (Format-Counts $s.articoli_per_livello))
    if ($s.per_lingua) { Write-Log ("  Affermazioni per lingua (it = italiano, en = inglese): {0}" -f (Format-Counts $s.per_lingua)) }
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)"; return }
    Write-Log ''
    Write-Log 'CONTROLLO A MANO DELLE AFFERMAZIONI (nel file solo quelle mai controllate)...'
    $c = Invoke-Orpheus @('affermazioni-controllo')
    if (-not $c.Json -or -not $c.Json.modo) { Show-Error $c; return }
    $k = $c.Json
    if ($k.modo -eq 'file da compilare') {
        Write-Log "  File da compilare ($($k.righe) righe): $($k.file_csv)"
        Write-Log '  Aprilo con Excel: per ogni riga guarda la pagina del PDF indicata e scrivi S o N nella colonna giusto'
        Write-Log '  (l''affermazione dice quello che dice la frase, e il livello segue le regole). Se N, scrivi il perche'' in note.'
        Write-Log "  Salvalo come: $($k.file_da_salvare)  (CSV), poi rilancia affermazioni.cmd."
    } elseif ($k.modo -eq 'risultati') {
        $pc = $k.precisione_complessiva
        Write-Log ("  Affermazioni controllate: giuste {0}, sbagliate {1}, non compilate {2}; precisione {3}" -f $pc.giusti, $pc.sbagliati, $pc.non_compilati, $pc.precisione)
        foreach ($p in $k.per_livello.PSObject.Properties) { Write-Log ("    livello {0}: giuste {1}, sbagliate {2}" -f $p.Name, $p.Value.giusti, $p.Value.sbagliati) }
        if ($k.per_lingua) { foreach ($p in $k.per_lingua.PSObject.Properties) { Write-Log ("    lingua {0}: giuste {1}, sbagliate {2}" -f $p.Name, $p.Value.giusti, $p.Value.sbagliati) } }
        foreach ($x in @($k.sbagliate | Select-Object -First 15)) { if ($x) { Write-Log "    ! riga $($x.n): $($x.affermazione) $($x.note)" } }
        Write-Log "  Giudizi nuovi registrati nel database: $($k.affermazioni_registrate)"
        if ($k.da_controllare -gt 0) { Write-Log "  Restano da controllare $($k.da_controllare) affermazioni (per esempio quelle nuove in inglese): rilancia affermazioni.cmd per avere il file." }
    } else { Write-Log "  $($k.motivo)" }
    Write-Log '  Scrivi a Claude: "affermazioni fatte".'
}

function Invoke-Lavora([string]$Gruppo = 'tutto') {
    Write-Log ''
    if ($Gruppo -eq 'tutto') {
        Write-Log 'LAVORA: ORPHEUS fa in ordine tutto quello che manca (engine-v1): lettura dei PDF nuovi, verifiche, riviste,'
        Write-Log 'schede e affermazioni scritte da Claude, pezzi di testo, vettori, report. Salta quello che e'' gia'' fatto.'
    } else {
        Write-Log "GRUPPO DI LAVORA '$Gruppo' (engine-v1): solo i passi di questo gruppo, in ordine, solo quello che manca."
    }
    Write-Log 'Se il PC si spegne o chiudi la finestra, al prossimo doppio clic riprende da dove si era fermato.'
    Write-Log ''
    $p = Invoke-Orpheus @('lavora', '--piano', '--gruppo', $Gruppo)
    if (-not $p.Json -or -not $p.Json.ordine) {
        Show-Error $p
        if ($p.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    if ($p.Json.titolo_gruppo) { Write-Log "  $($p.Json.titolo_gruppo): $(@($p.Json.ordine).Count) passi" }
    if ($p.Json.in_corso) {
        Write-Log "  lavora.cmd e' gia' in corso su questo PC (dal $($p.Json.in_corso.quando), passo: $($p.Json.in_corso.passo)): aspetta che finisca."
        return
    }
    foreach ($x in @($p.Json.interrotti)) {
        if ($x) { Write-Log "  RIPRESA: il passo '$($x.titolo)' non era finito (iniziato il $($x.quando)): si rifa' da quello che manca." }
    }
    Write-Log 'COPIA DI SICUREZZA DEL DATABASE'
    $b = Invoke-Orpheus @('backup')
    if ($b.Code -ne 0 -or -not $b.Json -or -not $b.Json.backup) {
        Show-Error $b
        Write-Log '  Niente fatto: prima serve una copia di sicurezza riuscita.'
        return
    }
    Write-Log "  Fatta: $($b.Json.backup)"
    Write-Log ''
    $giro = Get-Date -Format 'yyyy-MM-dd_HHmmss'
    $titoli = $p.Json.titoli
    $fatti = 0
    $fermo = $false
    foreach ($k in @($p.Json.ordine)) {
        Write-Host ("  ... controllo: {0}" -f $titoli.$k)
        $r = Invoke-Orpheus @('lavora', '--passo', $k, '--giro', $giro, '--pid', "$PID")
        if (-not $r.Json -or -not $r.Json.esito) {
            Show-Error $r
            if ($r.Code -eq 4 -or $r.Code -eq 6) { $fermo = $true; break }
            continue
        }
        $j = $r.Json
        foreach ($x in @($j.interrotti_ripresi)) { if ($x) { Write-Log "  Chiuso il passo interrotto '$($x.titolo)' (iniziato il $($x.quando)): ora si rifa'." } }
        if ($j.esito -eq 'niente da fare') { continue }
        if ($j.esito -eq 'rimandato') { Write-Log "  ($($j.titolo): $($j.motivo))"; continue }
        $fatti++
        Write-Log ("{0}. {1}: {2}  (da fare: {3} {4}; tentativo {5})" -f $fatti, $j.titolo, $j.esito.ToUpper(), $j.da_fare, $j.unita, $j.tentativo)
        if ($j.riassunto) { Write-Log "     $($j.riassunto)" }
        if ($null -ne $j.da_fare_dopo -and $j.da_fare_dopo -gt 0) { Write-Log "     Resta da fare: $($j.da_fare_dopo) $($j.unita) (si riprova al prossimo lavora.cmd)" }
        foreach ($e in @($j.errori | Select-Object -First 5)) { if ($e) { Write-Log "     ! $e" } }
        if ($j.esito -eq 'fermo') {
            Write-Log '  ATTENZIONE: la copia del database e'' piu'' vecchia o diversa da quella gia'' vista su questo PC: lavora si ferma.'
            Write-Log '  Scrivi a Claude prima di continuare.'
            $fermo = $true
            break
        }
    }
    Write-Log ''
    if ($fatti -eq 0 -and -not $fermo) { Write-Log 'Niente da fare: e'' tutto aggiornato.' }
    $f = Invoke-Orpheus @('lavora', '--fine', '--giro', $giro)
    if ($f.Json -and $null -ne $f.Json.per_claude) {
        $pc = @($f.Json.per_claude | Where-Object { $_ })
        $pt = @($f.Json.per_te | Where-Object { $_ })
        if ($pt.Count -gt 0) {
            Write-Log 'DA FARE PER TE:'
            foreach ($x in $pt) {
                Write-Log "  - $($x.lavoro): $($x.quanti)"
                if ($x.file) { Write-Log "    file: $($x.file)" }
                Write-Log "    $($x.come)"
            }
        }
        if ($pc.Count -gt 0) {
            Write-Log 'DA FARE PER CLAUDE (in una sessione):'
            foreach ($x in $pc) { Write-Log "  - $($x.lavoro): $($x.quanti)" }
        }
    } else { Show-Error $f }
    Write-Log '  Scrivi a Claude: "lavora fatto".'
}

function Invoke-Manoscritto {
    Write-Log ''
    Write-Log 'CONTROLLO DI UN MANOSCRITTO (.docx) RISPETTO ALLE REGOLE DELLE RIVISTE'
    Write-Log 'Il testo e il nome del file restano su questo PC: nel registro vanno solo numeri ed esiti.'
    $r = Invoke-Orpheus @('manoscritto', '--riviste')
    if (-not $r.Json -or $null -eq $r.Json.riviste) { Show-Error $r; return }
    $journals = @(@($r.Json.riviste) | Where-Object { $_ })
    if ($journals.Count -eq 0) { Write-Log '  Nessuna rivista con la scheda: prima schede_riviste.cmd.'; return }
    $folder = $r.Json.cartella_manoscritti
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $files = @(Get-ChildItem -LiteralPath $folder -File -Filter '*.docx' | Where-Object { -not $_.Name.StartsWith('~$') } |
               Sort-Object LastWriteTime -Descending)
    if ($files.Count -eq 0) {
        Write-Log "  Nessun manoscritto nella cartella $folder"
        Write-Log '  Copia li'' il file Word (.docx) del manoscritto e rilancia manoscritto.cmd. Apro la cartella.'
        try { Start-Process explorer.exe $folder } catch { Write-Log '  (apri la cartella a mano)' }
        return
    }
    # I nomi dei file si mostrano solo sullo schermo (Write-Host), mai nel registro.
    Write-Host ''
    Write-Host 'Manoscritti nella cartella (il piu'' recente per primo):'
    for ($i = 0; $i -lt $files.Count; $i++) {
        Write-Host ("  {0}. {1}   ({2:yyyy-MM-dd HH:mm})" -f ($i + 1), $files[$i].Name, $files[$i].LastWriteTime)
    }
    $pick = 1
    if ($files.Count -gt 1) {
        $ans = Read-Host 'Numero del manoscritto (solo Invio = 1)'
        if ($ans -and $ans.Trim()) {
            if (-not [int]::TryParse($ans.Trim(), [ref]$pick) -or $pick -lt 1 -or $pick -gt $files.Count) {
                Write-Log '  Numero non valido: rilancia manoscritto.cmd.'; return
            }
        }
    }
    $file = $files[$pick - 1]
    Write-Host ''
    Write-Host 'Riviste con la scheda:'
    for ($i = 0; $i -lt $journals.Count; $i++) {
        Write-Host ("  {0}. {1} - {2}" -f ($i + 1), $journals[$i].sigla, $journals[$i].titolo)
    }
    $ans = Read-Host 'Numero della rivista, o piu'' numeri separati da virgole (solo Invio = tutte, a confronto)'
    $argList = @('manoscritto')
    $sigle = @()
    if ($ans -and $ans.Trim()) {
        foreach ($part in ($ans -split '[,; ]+' | Where-Object { $_ })) {
            $k = 0
            if (-not [int]::TryParse($part, [ref]$k) -or $k -lt 1 -or $k -gt $journals.Count) {
                Write-Log "  Numero di rivista non valido ($part): rilancia manoscritto.cmd."; return
            }
            $argList += @('--rivista', $journals[$k - 1].sigla)
            $sigle += $journals[$k - 1].sigla
        }
    }
    $ans = Read-Host 'Tipo: 1 = studio originale, 2 = revisione sistematica o meta-analisi, 3 = protocollo (solo Invio = lo stima ORPHEUS)'
    $tipo = 'auto'
    if ($ans -and $ans.Trim() -eq '1') { $tipo = 'originale' } elseif ($ans -and $ans.Trim() -eq '2') { $tipo = 'revisione' }
    elseif ($ans -and $ans.Trim() -eq '3') { $tipo = 'protocollo' }
    $argList += @('--tipo', $tipo)
    # T-038: a Crossref vanno solo i DOI e i titoli dei lavori citati, mai il testo del manoscritto (R10).
    $ans = Read-Host 'Verificare i riferimenti con Crossref? Vanno in rete solo DOI e titoli dei lavori citati. S/N (solo Invio = N)'
    $verifica = ($ans -and $ans.Trim() -match '^[sSyY]')
    if ($verifica) { $argList += @('--verifica-riferimenti') }
    Write-Log ''
    $con = if ($sigle.Count -gt 0) { $sigle -join ', ' } else { 'tutte le riviste' }
    Write-Log "  Controllo del manoscritto n. $pick con: $con (tipo: $tipo)"
    $env:ORPHEUS_MANOSCRITTO = $file.FullName          # il percorso passa dall'ambiente: non finisce nel registro
    try { $r = Invoke-Orpheus $argList } finally { Remove-Item Env:ORPHEUS_MANOSCRITTO -ErrorAction SilentlyContinue }
    if (-not $r.Json -or $null -eq $r.Json.riviste) { Show-Error $r; return }
    $m = $r.Json.manoscritto
    Write-Log ("  Manoscritto (impronta {0}): {1} parole nel testo, abstract {2} parole, {3} parole chiave, {4} riferimenti ({5} con DOI), {6} figure, {7} tabelle; citazioni {8}" -f $m.impronta, $m.parole_testo, $m.parole_abstract, $m.parole_chiave, $m.riferimenti, $m.riferimenti_con_doi, $m.figure, $m.tabelle, $m.citazioni)
    $how = if ($r.Json.scelto_da -eq 'utente') { 'scelto da te' } else { 'stimato da ORPHEUS' }
    Write-Log "  Tipo di articolo: $($r.Json.tipo) ($how)"
    if ($m.lingua -and $m.lingua -ne 'en') { Write-Log '  ATTENZIONE: il testo non sembra in inglese.' }
    Write-Log ''
    Write-Log '  Rivista   da correggere   da guardare   conformi'
    foreach ($j in @($r.Json.riviste)) {
        if (-not $j) { continue }
        $c = $j.conteggi
        Write-Log ("  {0,-8} {1,8} {2,14} {3,11}" -f $j.rivista, $c.'da correggere', $c.'da guardare', $c.conforme)
        foreach ($t in @($j.da_correggere)) { if ($t) { Write-Log "             - da correggere: $t" } }
    }
    if ($r.Json.verifica_riferimenti) {
        $v = $r.Json.verifica_riferimenti.conteggi
        Write-Log ''
        Write-Log ("  Riferimenti (ORPHEUS e Crossref): {0} verificati, {1} discordanti, {2} non trovati, {3} non verificati; richieste a Crossref {4}, dalla cache {5}" -f $v.verificato, $v.discordante, $v.'non trovato', $v.'non verificato', $r.Json.verifica_riferimenti.richieste_crossref, $r.Json.verifica_riferimenti.dalla_cache)
        if ($r.Json.verifica_riferimenti.interrotta) { Write-Log '  ATTENZIONE: verifica interrotta da Crossref (accesso bloccato): riprova piu'' tardi.' }
    }
    if (@($r.Json.riviste_sconosciute).Count -gt 0 -and $r.Json.riviste_sconosciute[0]) {
        Write-Log "  Riviste senza scheda (saltate): $(@($r.Json.riviste_sconosciute) -join ', ')"
    }
    Write-Log ''
    Write-Log '  Compatibilita'' con le regole delle riviste, non probabilita'' di accettazione.'
    if ($r.Json.rapporto) {
        Write-Log "  Rapporto completo, con la regola e la pagina delle linee guida per ogni controllo (resta su questo PC):"
        Write-Log "  $($r.Json.rapporto)"
        try { Start-Process $r.Json.rapporto } catch { Write-Log '  (aprilo a mano con il browser)' }
    }
}

# ---------------------------------------------------------------- scheda unica dell'articolo (EURYDIKE E2, T-051c, cards-v2)
function Invoke-SchedaUnica {
    Write-Log ''
    Write-Log "SCHEDA UNICA DELL'ARTICOLO (cards-v2: la usano ORPHEUS ed EURYDIKE; le schede cards-v1 non vengono cambiate; prima una copia di sicurezza)"
    $r = Invoke-Orpheus @('scheda-unica', '--converti', '--tutte')
    if ($r.Code -ne 0 -or -not $r.Json -or -not $r.Json.conversione) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $c = $r.Json.conversione
    if ($r.Json.backup_prima_della_scrittura) { Write-Log "  Copia di sicurezza: $($r.Json.backup_prima_della_scrittura)" }
    Write-Log ("  Schede cards-v1: {0} - convertite ora: {1} - gia' convertite: {2}" -f $c.schede_v1, $c.convertite, $c.gia_convertite)
    Write-Log ("  Valori copiati: {0} (confermati dal tuo controllo a mano: {1}; segnati sbagliati, da correggere: {2})" -f $c.valori_copiati, $c.confermati_dal_controllo, $c.segnati_sbagliati)
    Write-Log ("  'Non riportato' proposti da Claude, non copiati (li decidi tu): {0} - tue decisioni tenute: {1}" -f $c.non_riportati_non_copiati, $c.tenute_tue_decisioni)
    Write-Log ("  Schede esportate: {0} (JSON + CSV) - valori corretti a mano: {1} - da controllare: {2}" -f $r.Json.esportate, $r.Json.corretti_a_mano, $r.Json.da_controllare)
    if ($r.Json.cartella) {
        Write-Log "  Cartella: $($r.Json.cartella)"
        try { Start-Process explorer.exe $r.Json.cartella } catch { Write-Log '  (apri la cartella a mano)' }
    }
}


# ---------------------------------------------------------------- condivisione con i colleghi (T-053)
function Invoke-EsportaSchede {
    Write-Log ''
    Write-Log 'ESPORTA LE SCHEDE PER I COLLEGHI (schede delle riviste e degli articoli, metadati, pezzi di testo; niente PDF, niente manoscritti; il database non cambia)'
    $r = Invoke-Orpheus @('condivisione-esporta')
    if ($r.Code -ne 0 -or -not $r.Json -or -not $r.Json.pacchetto) { Show-Error $r; return }
    $s = $r.Json.riepilogo
    Write-Log ("  Riviste {0} (schede {1}) - articoli {2} (schede {3}) - pezzi di testo {4} - affermazioni {5}" -f $s.riviste, $s.schede_riviste, $s.articoli, $s.schede_articoli, $s.pezzi_di_testo, $s.affermazioni)
    Write-Log ("  File: {0} ({1} MB)" -f $r.Json.pacchetto, $r.Json.dimensione_mb)
    Write-Log '  Mandalo al collega (mail, chiavetta, cartella condivisa): lui lo mette in ORPHEUS_DATI\inbox\condivisione e fa doppio clic su importa_schede.cmd.'
    Write-Log '  Il file contiene testo di articoli non aperti: va solo a colleghi con gli stessi accessi, MAI su GitHub.'
    try { Start-Process explorer.exe ('/select,"' + $r.Json.pacchetto + '"') } catch { }
}

function Invoke-ImportaSchede {
    Write-Log ''
    Write-Log "IMPORTA LE SCHEDE DI UN COLLEGA (quello che hai gia' resta tuo: si aggiunge solo quello che manca)"
    $argsProva = @('condivisione-unisci', '--prova')
    if ($File) { $argsProva += $File }
    $p = Invoke-Orpheus $argsProva
    if ($p.Code -ne 0 -and $p.Code -ne 3 -or -not $p.Json -or -not $p.Json.riepilogo) {
        Show-Error $p
        if ($p.Raw -match 'migrate') { Write-Log "  Aggiorna prima ORPHEUS (aggiorna.cmd o installa_orpheus.cmd)." }
        return
    }
    $s = $p.Json.riepilogo
    Write-Log "  Pacchetto: $($p.Json.pacchetto) (creato il $($p.Json.creato_il))"
    Write-Log ("  Entrerebbero: riviste nuove {0}, schede delle riviste {1}, articoli nuovi {2}, schede degli articoli {3}, pezzi di testo {4}" -f $s.riviste_nuove, $s.schede_riviste_nuove, $s.articoli_nuovi, $s.schede_articoli_nuove, $s.pezzi_di_testo_nuovi)
    Write-Log ("  Schede che hai gia' (restano le tue): {0}" -f $s.schede_gia_presenti_tenute_le_tue)
    if ($p.Json.problemi_totali -gt 0) { Write-Log ("  Righe che non possono entrare: {0} (dettagli nel registro)" -f $p.Json.problemi_totali) }
    if (-not ($p.Json.nuove.PSObject.Properties | Select-Object -First 1)) { Write-Log "  Niente di nuovo: hai gia' tutto quello che c'e' nel pacchetto."; return }
    $a = Read-Host '  Importo adesso? (prima faccio una copia di sicurezza)  S/N'
    if ("$a".Trim().ToUpper() -ne 'S') { Write-Log '  Non importato.'; return }
    $argsVero = @('condivisione-unisci', $p.Json.pacchetto)
    $r = Invoke-Orpheus $argsVero
    if (-not $r.Json -or -not $r.Json.riepilogo) { Show-Error $r; return }
    Write-Log "  Copia di sicurezza: $($r.Json.copia_di_sicurezza)"
    Write-Log ("  Importato: {0}" -f (Format-Counts $r.Json.nuove))
    Write-Log ("  Controllo del database: {0}, collegamenti rotti {1}" -f $r.Json.controllo.integrity, $r.Json.controllo.foreign_key_violations)
}

# ---------------------------------------------------------------- controllo di una revisione sistematica (EURYDIKE E1, T-051b)
function Invoke-RevisioneSistematica {
    Write-Log ''
    Write-Log 'CONTROLLO DI UNA REVISIONE SISTEMATICA O META-ANALISI (.docx o .pdf), SENZA RIVISTA (EURYDIKE, review-audit-v1)'
    Write-Log 'Il testo e il nome del file restano su questo PC: nel registro vanno solo numeri ed esiti.'
    $r = Invoke-Orpheus @('revisione-sistematica', '--cartella')
    if (-not $r.Json -or -not $r.Json.cartella_manoscritti) { Show-Error $r; return }
    $folder = $r.Json.cartella_manoscritti
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $files = @(Get-ChildItem -LiteralPath $folder -File | Where-Object { ($_.Extension -eq '.docx' -or $_.Extension -eq '.pdf') -and -not $_.Name.StartsWith('~$') } |
               Sort-Object LastWriteTime -Descending)
    if ($files.Count -eq 0) {
        Write-Log "  Nessun file nella cartella $folder"
        Write-Log '  Copia li'' la revisione in Word (.docx) o in PDF e rilancia revisione_sistematica.cmd. Apro la cartella.'
        try { Start-Process explorer.exe $folder } catch { Write-Log '  (apri la cartella a mano)' }
        return
    }
    # I nomi dei file si mostrano solo sullo schermo (Write-Host), mai nel registro.
    Write-Host ''
    Write-Host 'File nella cartella (il piu'' recente per primo):'
    for ($i = 0; $i -lt $files.Count; $i++) {
        Write-Host ("  {0}. {1}   ({2:yyyy-MM-dd HH:mm})" -f ($i + 1), $files[$i].Name, $files[$i].LastWriteTime)
    }
    $pick = 1
    if ($files.Count -gt 1) {
        $ans = Read-Host 'Numero della revisione (solo Invio = 1)'
        if ($ans -and $ans.Trim()) {
            if (-not [int]::TryParse($ans.Trim(), [ref]$pick) -or $pick -lt 1 -or $pick -gt $files.Count) {
                Write-Log '  Numero non valido: rilancia revisione_sistematica.cmd.'; return
            }
        }
    }
    $file = $files[$pick - 1]
    Write-Log "  Controllo della revisione n. $pick"
    $env:ORPHEUS_MANOSCRITTO = $file.FullName          # il percorso passa dall'ambiente: non finisce nel registro
    try { $r = Invoke-Orpheus @('revisione-sistematica') } finally { Remove-Item Env:ORPHEUS_MANOSCRITTO -ErrorAction SilentlyContinue }
    if (-not $r.Json -or $null -eq $r.Json.conteggi) { Show-Error $r; return }
    $m = $r.Json.manoscritto
    Write-Log ("  Revisione (impronta {0}): {1} parole nel testo, abstract {2} parole, {3} riferimenti, {4} tabelle, {5} figure" -f $m.impronta, $m.parole_testo, $m.parole_abstract, $m.riferimenti, $m.tabelle, $m.figure)
    $f = $r.Json.numeri.flusso
    $parts = @()
    foreach ($k in @('identified', 'duplicates', 'screened', 'excluded', 'sought', 'not_retrieved', 'assessed', 'ft_excluded', 'included', 'ma')) {
        $v = $f.$k
        if ($null -ne $v) { $parts += "$k $v" }
    }
    if ($r.Json.formato -eq 'pdf') { Write-Log '  Letta dal PDF: tabelle e diagrammi in immagine non si leggono (controlla a mano i loro numeri).' }
    if ($parts.Count -gt 0) { Write-Log ("  Numeri del flusso PRISMA trovati: " + ($parts -join ', ')) }
    else { Write-Log '  Nessun numero del flusso PRISMA trovato nel testo (se il diagramma e'' un''immagine, controllalo a mano).' }
    $c = $r.Json.conteggi
    Write-Log ("  Da correggere {0}, da guardare {1}, conformi {2}" -f $c.'da correggere', $c.'da guardare', $c.conforme)
    foreach ($t in @($r.Json.da_correggere)) { if ($t) { Write-Log "    - da correggere: $t" } }
    foreach ($t in @($r.Json.da_guardare)) { if ($t) { Write-Log "    - da guardare: $t" } }
    Write-Log ''
    Write-Log '  Controllo della coerenza interna, non della qualita'' delle prove.'
    if ($r.Json.rapporto) {
        Write-Log '  Rapporto completo (resta su questo PC):'
        Write-Log "  $($r.Json.rapporto)"
        try { Start-Process $r.Json.rapporto } catch { Write-Log '  (aprilo a mano con il browser)' }
    }
}

# ---------------------------------------------------------------- schede con un modello locale (prova pilota, ADR-0010, T-042)
function Invoke-SchedeLocali {
    Write-Log ''
    Write-Log 'SCHEDE CON UN MODELLO LOCALE (prova pilota, ADR-0010): Ollama su questo PC, nessun servizio a pagamento.'
    Write-Log '  Le schede vanno solo in rapporti\schede\locali: NON entrano nel database (si confrontano a mano).'
    Write-Log '  Senza scheda grafica e'' lento: circa 5 minuti per pezzo, 20-30 minuti per articolo (10 articoli: alcune ore).'
    Write-Log '  Qui sotto vedi a che punto e''. Puoi chiudere la finestra quando vuoi: le schede finite restano e la volta'
    Write-Log '  dopo si riparte dall''articolo successivo (si perde solo quello in corso).'
    $r = Invoke-Orpheus @('schede-locali', '--modello', 'qwen2.5:7b-instruct', '--quanti', '10') -Live
    if (-not $r.Json -or $null -eq $r.Json.pacchetti_fatti) {
        Show-Error $r
        if (($r.Raw + $r.Err) -match 'non risponde') {
            Write-Log '  Installa Ollama da https://ollama.com/download (gratuito), avvialo, poi in un Prompt dei comandi:'
            Write-Log '    ollama pull qwen2.5:7b-instruct'
            Write-Log '  e rilancia schede_locali.cmd.'
        } elseif (($r.Raw + $r.Err) -match 'ollama pull') {
            Write-Log '  In un Prompt dei comandi: ollama pull qwen2.5:7b-instruct  (circa 4,7 GB), poi rilancia schede_locali.cmd.'
        }
        return
    }
    $j = $r.Json
    Write-Log ("  Modello {0}: pacchetti fatti {1}, schede accettabili {2}, non riuscite {3}, tempo {4} min" -f $j.modello, $j.pacchetti_fatti, $j.accettabili, $j.non_riuscite, [math]::Round($j.secondi_totali / 60))
    foreach ($c in @($j.schede)) {
        if ($c) {
            $extra = ''
            if ($c.pezzi_falliti -or $c.pezzi_tagliati) { $extra = ", pezzi saltati $($c.pezzi_falliti) e tagliati $($c.pezzi_tagliati) su $($c.pezzi)" }
            Write-Log ("    - {0}: {1} (valori con citazione {2}, scartati {3}{4}, {5} min)" -f $c.article_id, $c.esito, $c.valori_con_citazione, $c.valori_scartati, $extra, [math]::Round($c.secondi / 60))
        }
    }
    if ($j.non_riuscite) { Write-Log '  Le schede non riuscite restano da fare: rilancia schede_locali.cmd quando il PC e'' libero.' }
    Write-Log "  Cartella: $($j.cartella)"
    Write-Log '  Scrivi a Claude: "schede locali fatte".'
}

# ---------------------------------------------------------------- revisione simulata del manoscritto (T-040 parte 3, review-v1)
function Select-ManuscriptJournal([string]$Cmd, [string]$What = 'manoscritto') {
    # Sceglie un manoscritto .docx della cartella manoscritti e UNA rivista con la scheda. I nomi dei file solo a
    # schermo (Write-Host), mai nel registro. Restituisce $null se manca qualcosa.
    # Dal cruscotto (2026-10-17): ORPHEUS_SCELTA_FILE (nome del file nella cartella) e ORPHEUS_SCELTA_RIVISTA (sigla)
    # arrivano dall'ambiente e non si chiede niente.
    $r = Invoke-Orpheus @('manoscritto', '--riviste')
    if (-not $r.Json -or $null -eq $r.Json.riviste) { Show-Error $r; return $null }
    $journals = @(@($r.Json.riviste) | Where-Object { $_ })
    if ($journals.Count -eq 0) { Write-Log '  Nessuna rivista con la scheda: prima schede_riviste.cmd.'; return $null }
    $folder = $r.Json.cartella_manoscritti
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    if ($env:ORPHEUS_SCELTA_FILE -and $env:ORPHEUS_SCELTA_RIVISTA) {
        $f = Join-Path $folder ([System.IO.Path]::GetFileName($env:ORPHEUS_SCELTA_FILE))
        $sg = @($journals | Where-Object { $_.sigla -eq $env:ORPHEUS_SCELTA_RIVISTA })
        if (-not (Test-Path -LiteralPath $f) -or $sg.Count -eq 0) {
            Write-Log '  Manoscritto o rivista scelti nel cruscotto non trovati: scegli di nuovo.'; return $null
        }
        Write-Log "  Manoscritto scelto nel cruscotto, rivista $($sg[0].sigla)"
        return [pscustomobject]@{ File = $f; Sigla = $sg[0].sigla }
    }
    $files = @(Get-ChildItem -LiteralPath $folder -File -Filter '*.docx' | Where-Object { -not $_.Name.StartsWith('~$') } |
               Sort-Object LastWriteTime -Descending)
    if ($files.Count -eq 0) {
        Write-Log "  Nessun manoscritto nella cartella $folder : copia li'' il file Word (.docx) e rilancia $Cmd."
        return $null
    }
    Write-Host ''
    Write-Host "Manoscritti nella cartella (il piu' recente per primo) - scegli il $What :"
    for ($i = 0; $i -lt $files.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $files[$i].Name) }
    $pick = 1
    if ($files.Count -gt 1) {
        $ans = Read-Host 'Numero del manoscritto (solo Invio = 1)'
        if ($ans -and $ans.Trim() -and (-not [int]::TryParse($ans.Trim(), [ref]$pick) -or $pick -lt 1 -or $pick -gt $files.Count)) {
            Write-Log "  Numero non valido: rilancia $Cmd."; return $null
        }
    }
    Write-Host ''
    Write-Host 'Riviste con la scheda:'
    for ($i = 0; $i -lt $journals.Count; $i++) { Write-Host ("  {0}. {1} - {2}" -f ($i + 1), $journals[$i].sigla, $journals[$i].titolo) }
    $k = 1
    $ans = Read-Host 'Numero della rivista (una sola; solo Invio = 1)'
    if ($ans -and $ans.Trim() -and (-not [int]::TryParse($ans.Trim(), [ref]$k) -or $k -lt 1 -or $k -gt $journals.Count)) {
        Write-Log "  Numero non valido: rilancia $Cmd."; return $null
    }
    Write-Log "  Manoscritto n. $pick, rivista $($journals[$k - 1].sigla)"
    return [pscustomobject]@{ File = $files[$pick - 1].FullName; Sigla = $journals[$k - 1].sigla }
}

function Invoke-OnManuscript([string[]]$ArgList, [string]$File) {
    $env:ORPHEUS_MANOSCRITTO = $File          # il percorso passa dall'ambiente: non finisce nel registro
    try { return Invoke-Orpheus $ArgList } finally { Remove-Item Env:ORPHEUS_MANOSCRITTO -ErrorAction SilentlyContinue }
}

function Invoke-Revisione {
    Write-Log ''
    Write-Log 'REVISIONE SIMULATA DI UN MANOSCRITTO (.docx) PER UNA RIVISTA (review-v1)'
    Write-Log 'Tre revisori immaginari (metodologo, statistico, editor) in una sessione con Claude: e'' una simulazione,'
    Write-Log 'non una previsione. Il manoscritto resta su questo PC: nel registro solo numeri ed esiti.'
    $sel = Select-ManuscriptJournal 'revisione.cmd'
    if (-not $sel) { return }
    $r = Invoke-OnManuscript @('revisione', '--rivista', $sel.Sigla) $sel.File
    if (-not $r.Json -or $null -eq $r.Json.revisione) { Show-Error $r; return }
    $j = $r.Json
    Write-Log ("  Manoscritto {0}: {1} paragrafi; controlli da correggere {2}, da guardare {3}" -f $j.impronta, $j.paragrafi, $j.controlli_da_correggere, $j.controlli_da_guardare)
    switch ($j.revisione) {
        'da scrivere' {
            Write-Log '  Pacchetto pronto. In una sessione con Claude, con il PC collegato, scrivi: "fai la revisione simulata".'
            Write-Log '  Poi rilancia revisione.cmd con lo stesso manoscritto e la stessa rivista.'
        }
        'rifiutata' {
            Write-Log ("  Revisione RIFIUTATA ({0} problemi): i motivi sono in revisione.motivi.txt; scrivi a Claude: ""correggi la revisione""." -f $j.problemi)
        }
        'accettata' {
            Write-Log ("  Revisione ACCETTATA: {0} osservazioni (maggiori {1}, minori {2}, suggerimenti {3})" -f $j.osservazioni, $j.maggiore, $j.minore, $j.suggerimento)
            foreach ($p in $j.raccomandazioni.PSObject.Properties) { Write-Log "    - $($p.Name): $($p.Value)" }
            Write-Log '  E'' una simulazione, non una previsione dell''esito della rivista.'
            try { Start-Process $j.rapporto } catch { Write-Log '  (apri revisione.html a mano)' }
        }
    }
    Write-Log "  Cartella: $($j.cartella)"
}

# ---------------------------------------------------------------- percorsi (2026-10-17: funzioni unite per ordine di uso)
function Invoke-Manutenzione {
    Write-Log ''
    Write-Log 'MANUTENZIONE: controllo generale, prova di ripristino dell''ultima copia di sicurezza, salute di ORPHEUS, report.'
    Write-Log 'Non modifica il database.'
    Invoke-Controllo
    Invoke-ProvaRipristino
    Write-Log ''
    Write-Log 'SALUTE DI ORPHEUS (health-v1)'
    $h = Invoke-Orpheus @('salute')
    if (-not $h.Json -or -not $h.Json.controlli) { Show-Error $h }
    else {
        foreach ($c in @($h.Json.controlli)) { if ($c) { Write-Log ("  {0,-22} {1,-10} {2}" -f $c.nome, $c.esito, $c.messaggio) } }
        Write-Log "  Esito complessivo: $($h.Json.esito)"
    }
    Invoke-Report
}

function Invoke-PreparaManoscritto {
    Write-Log ''
    Write-Log 'PREPARA UN MANOSCRITTO PER UNA RIVISTA: controllo con la verifica dei riferimenti (Crossref: solo DOI e titoli'
    Write-Log 'citati), pacchetti per Claude della revisione simulata e della lettera, file di validazione.'
    Write-Log 'Il manoscritto resta su questo PC: nel registro solo numeri ed esiti. Il database non cambia.'
    $sel = Select-ManuscriptJournal 'prepara_manoscritto.cmd'
    if (-not $sel) { return }
    Write-Log ''
    Write-Log '1. CONTROLLO DEL MANOSCRITTO'
    $r = Invoke-OnManuscript @('manoscritto', '--rivista', $sel.Sigla, '--verifica-riferimenti') $sel.File
    if (-not $r.Json -or $null -eq $r.Json.riviste) { Show-Error $r; return }
    foreach ($j in @($r.Json.riviste)) {
        if ($j) { Write-Log ("  {0}: da correggere {1}, da guardare {2}, conformi {3}" -f $j.rivista, $j.conteggi.'da correggere', $j.conteggi.'da guardare', $j.conteggi.conforme) }
    }
    if ($r.Json.verifica_riferimenti) {
        $v = $r.Json.verifica_riferimenti.conteggi
        Write-Log ("  Riferimenti: {0} verificati, {1} discordanti, {2} non trovati, {3} non verificati" -f $v.verificato, $v.discordante, $v.'non trovato', $v.'non verificato')
    }
    if ($r.Json.rapporto) { Write-Log "  Rapporto: $($r.Json.rapporto)" }
    Write-Log ''
    Write-Log '2. REVISIONE SIMULATA (review-v1)'
    $v = Invoke-OnManuscript @('revisione', '--rivista', $sel.Sigla) $sel.File
    if ($v.Json -and $v.Json.revisione) { Write-Log "  Revisione: $($v.Json.revisione)" } else { Show-Error $v }
    Write-Log ''
    Write-Log '3. LETTERA DI PRESENTAZIONE (cover-v1)'
    $l = Invoke-OnManuscript @('lettera', '--rivista', $sel.Sigla) $sel.File
    if ($l.Json -and $l.Json.esito) { Write-Log "  Lettera: $($l.Json.esito)" } else { Show-Error $l }
    Write-Log ''
    Write-Log '4. VALIDAZIONE (validation-v1)'
    $w = Invoke-OnManuscript @('validazione', '--rivista', $sel.Sigla) $sel.File
    if ($w.Json -and $w.Json.criteri) { Write-Log ("  File di controllo: {0} righe; gate: {1}" -f $w.Json.righe, $w.Json.gate) } else { Show-Error $w }
    Write-Log ''
    $ask = @()
    if ($v.Json -and $v.Json.revisione -eq 'da scrivere') { $ask += '"fai la revisione simulata"' }
    if ($l.Json -and $l.Json.esito -eq 'da scrivere') { $ask += '"scrivi la lettera di presentazione"' }
    if ($ask.Count -gt 0) {
        Write-Log ("  In una sessione con Claude, con il PC collegato, scrivi: {0}. Poi rilancia prepara_manoscritto.cmd." -f ($ask -join ' e '))
    } else {
        Write-Log '  Revisione e lettera sono pronte (revisione.html, lettera.docx nelle cartelle di rapporti\manoscritti).'
    }
}

function Invoke-Validazione {
    Write-Log ''
    Write-Log 'VALIDAZIONE DELLA FASE 7 SU UN MANOSCRITTO VERO (validation-v1)'
    Write-Log 'Controlli tu, riga per riga, quello che ORPHEUS ha detto del manoscritto (riferimenti, regole della rivista,'
    Write-Log 'numeri, revisione simulata); ORPHEUS calcola la precisione con l''intervallo al 95%. Il manoscritto resta su'
    Write-Log 'questo PC: nel registro solo numeri ed esiti. Il database non cambia.'
    $sel = Select-ManuscriptJournal 'validazione.cmd'
    if (-not $sel) { return }
    $r = Invoke-OnManuscript @('validazione', '--rivista', $sel.Sigla) $sel.File
    if (-not $r.Json -or $null -eq $r.Json.criteri) { Show-Error $r; return }
    $j = $r.Json
    Write-Log ("  Manoscritto {0}: {1} righe da controllare" -f $j.impronta, $j.righe)
    foreach ($p in $j.righe_per_criterio.PSObject.Properties) { Write-Log "    $($p.Name): $($p.Value) righe" }
    if (@($j.fonti_mancanti).Count -gt 0) {
        Write-Log ("  Mancano: {0} (B1 = rilancia manoscritto.cmd e rispondi S alla verifica dei riferimenti; C1 = revisione.cmd)" -f (@($j.fonti_mancanti) -join ', '))
    }
    Write-Log '  Criteri:'
    foreach ($p in $j.criteri.PSObject.Properties) {
        $v = $p.Value
        $extra = ''
        if ($null -ne $v.precisione) { $extra = (" precisione {0} (IC 95% {1}-{2}; {3} si', {4} no)" -f $v.precisione, $v.ic95[0], $v.ic95[1], $v.si, $v.no) }
        Write-Log ("    {0}: {1}{2}" -f $p.Name, $v.esito, $extra)
    }
    Write-Log "  Gate: $($j.gate)"
    if (-not $j.compilato) {
        Write-Log '  Apri controllo_validazione.csv (apro la cartella), scrivi S o N in giudizio (e in inventato per le righe C1),'
        Write-Log '  salvalo con _compilato alla fine del nome, compila mancati.txt e rilancia validazione.cmd.'
        try { Start-Process $j.cartella } catch { Write-Log '  (apri la cartella a mano)' }
    } else {
        Write-Log '  Scrivi a Claude: "validazione fatta".'
    }
    Write-Log "  Cartella: $($j.cartella)"
}

function Show-LetterOutcome($j, [string]$Cmd, [string]$Ask, [string]$Name) {
    switch ($j.esito) {
        'da scrivere' {
            Write-Log "  Pacchetto pronto. In una sessione con Claude, con il PC collegato, scrivi: ""$Ask""."
            Write-Log "  Poi rilancia $Cmd con lo stesso manoscritto e la stessa rivista."
        }
        'rifiutata' {
            Write-Log ("  RIFIUTATA ({0} problemi): i motivi sono nella cartella da_inviare; scrivi a Claude: ""correggi {1}""." -f $j.problemi, $Name)
        }
        'accettata' {
            Write-Log "  ACCETTATA: il file Word e' pronto. Controllalo e completalo (firma, nomi) prima dell'invio."
            try { Start-Process $j.documento } catch { Write-Log '  (apri il file a mano)' }
        }
    }
    Write-Log "  Cartella: $($j.cartella)"
}

function Invoke-Lettera {
    Write-Log ''
    Write-Log 'LETTERA DI PRESENTAZIONE PER LA RIVISTA (cover-v1), scritta da Claude in sessione e controllata da ORPHEUS.'
    Write-Log 'Il manoscritto resta su questo PC: nel registro solo numeri ed esiti.'
    $sel = Select-ManuscriptJournal 'lettera.cmd'
    if (-not $sel) { return }
    $r = Invoke-OnManuscript @('lettera', '--rivista', $sel.Sigla) $sel.File
    if (-not $r.Json -or $null -eq $r.Json.esito) { Show-Error $r; return }
    Write-Log ("  Manoscritto {0} ({1}); regole della rivista per la lettera: {2}" -f $r.Json.impronta, $r.Json.tipo, $r.Json.regole_lettera)
    Show-LetterOutcome $r.Json 'lettera.cmd' 'scrivi la lettera di presentazione' 'la lettera'
}

function Invoke-Risposta {
    Write-Log ''
    Write-Log 'RISPOSTA AI REVISORI (response-v1): dai commenti della rivista e dal manoscritto RIVEDUTO.'
    Write-Log 'Commenti e manoscritto restano su questo PC: nel registro solo numeri ed esiti.'
    $sel = Select-ManuscriptJournal 'risposta.cmd' 'manoscritto RIVEDUTO'
    if (-not $sel) { return }
    $r = Invoke-OnManuscript @('risposta', '--rivista', $sel.Sigla) $sel.File
    if (-not $r.Json -or $null -eq $r.Json.esito) { Show-Error $r; return }
    $j = $r.Json
    if ($j.esito -eq 'servono i commenti') {
        Write-Log '  Incolla i commenti dei revisori in commenti_revisori.txt (apro la cartella): una riga "Reviewer 1",'
        Write-Log '  "Reviewer 2" o "Editor" prima dei commenti di ognuno, ogni commento con il suo numero. Salva e rilancia risposta.cmd.'
        try { Start-Process -FilePath $j.cartella } catch { Write-Log "  (apri a mano: $($j.cartella))" }
        return
    }
    Write-Log ("  Commenti letti: {0} (revisori: {1})" -f $j.commenti, (@($j.revisori) -join ', '))
    Show-LetterOutcome $j 'risposta.cmd' 'scrivi la risposta ai revisori' 'la risposta'
}

# ---------------------------------------------------------------- schede con una chat gratuita usata a mano (ADR-0011, T-043)
# ---------------------------------------------------------------- schede con una chat gratuita (emergenza)
# Decisione dell'utente del 2026-10-17 19:13: strada di emergenza (pochi articoli o quando Claude non puo' lavorare);
# la strada principale resta Claude in sessione. La finestra guida passo per passo con gli appunti di Windows: copia i
# messaggi uno alla volta, legge la risposta copiata dalla chat, copia la correzione. Senza appunti (o con
# ORPHEUS_SENZA_APPUNTI) si usano i file della cartella come prima.
$ChatNames = @('ChatGPT gratuito', 'Perplexity gratuito', 'NotebookLM', 'Gemini gratuito')

function Set-Clip([string]$Text) {
    if ($env:ORPHEUS_SENZA_APPUNTI) { return $false }
    try { Set-Clipboard -Value $Text -ErrorAction Stop; return $true } catch { return $false }
}

function Get-Clip {
    if ($env:ORPHEUS_SENZA_APPUNTI) { return '' }
    try { return [string](Get-Clipboard -Raw -ErrorAction Stop) } catch { return '' }
}

function Select-Chat([string]$Last, [string]$Dir) {
    Write-Host ''
    Write-Host '  Quale chat usi?'
    for ($i = 0; $i -lt $ChatNames.Count; $i++) { Write-Host ("    {0}) {1}" -f ($i + 1), $ChatNames[$i]) }
    Write-Host ("    {0}) un'altra (scrivi il nome)" -f ($ChatNames.Count + 1))
    $hint = if ($Last) { " (solo Invio = $Last)" } else { '' }
    $a = "$(Read-Host "  Numero$hint")".Trim()
    $k = 0
    if (-not $a -and $Last) { $chat = $Last }
    elseif ([int]::TryParse($a, [ref]$k) -and $k -ge 1 -and $k -le $ChatNames.Count) { $chat = $ChatNames[$k - 1] }
    elseif ($a -and -not [int]::TryParse($a, [ref]$k)) { $chat = $a }
    else { $chat = "$(Read-Host '  Nome della chat (es. ChatGPT gratuito)')".Trim() }
    if (-not $chat -or $chat.Length -lt 3) { $chat = $ChatNames[0] }
    Add-Content -Path $LogFile -Value "  Chat dichiarata: $chat" -Encoding UTF8
    if ($Dir) {                                   # la prossima volta Invio = questa chat
        try { New-Item -ItemType Directory -Force -Path $Dir | Out-Null; Set-Content -Path (Join-Path $Dir 'modello.txt') -Value $chat -Encoding UTF8 -NoNewline } catch { }
    }
    return $chat
}

function Show-ChatState {
    $st = Invoke-Orpheus @('chat-schede', '--stato')
    if (-not $st.Json -or $null -eq $st.Json.preparati) { Show-Error $st; return $null }
    $j = $st.Json
    Write-Log ("  Articoli preparati {0}: da inviare al database {1}, gia' nel database {2}, rifiutate {3}, in attesa della risposta {4}; pacchetti ancora da preparare {5}" -f `
               $j.preparati, $j.da_inviare, $j.inviate, $j.rifiutate, ([int]$j.in_attesa + [int]$j.da_leggere), $j.ancora_da_preparare)
    return $j
}

function Invoke-ChatArticolo([string]$Folder, [string]$Id, [string]$Chat, [switch]$Resume) {
    # Un articolo, dall'inizio (o dalla risposta, se e' gia' stato incollato tutto) fino all'esito.
    $parts = @(Get-ChildItem -Path $Folder -Filter 'parte_*.txt' | Sort-Object Name)
    $answerFile = Join-Path $Folder 'risposta.txt'
    if (-not $Resume) {
        Write-Log ''
        Write-Log "  ARTICOLO $Id - $($parts.Count) messaggi da incollare in una chat NUOVA di $Chat"
        for ($i = 0; $i -lt $parts.Count; $i++) {
            $text = Get-Content -Raw -Encoding UTF8 -Path $parts[$i].FullName
            $last = ($i -eq $parts.Count - 1)
            $wait = if ($last) { "la chat ha scritto la scheda (blocco JSON)" } else { "la chat ha risposto OK" }
            if (Set-Clip $text) {
                Read-Host ("  [{0}/{1}] Messaggio copiato negli appunti: incollalo nella chat (Ctrl+V) e invialo. Quando {2}, premi Invio qui" -f ($i + 1), $parts.Count, $wait) | Out-Null
            } else {
                Read-Host ("  [{0}/{1}] Apri {2}, copia tutto e incollalo nella chat. Quando {3}, premi Invio qui" -f ($i + 1), $parts.Count, $parts[$i].Name, $wait) | Out-Null
            }
        }
    } else {
        Write-Log ''
        Write-Log "  ARTICOLO $Id - si riprende dalla risposta della chat ($Chat)"
        $fix0 = Join-Path $Folder 'correzione.txt'
        if (Test-Path $fix0) {
            if (Set-Clip (Get-Content -Raw -Encoding UTF8 -Path $fix0)) { Write-Log '  L''ultima correzione e'' negli appunti: se non l''hai gia'' fatto, incollala nella STESSA chat.' }
            else { Write-Log "  Se non l'hai gia' fatto, incolla nella STESSA chat il contenuto di $fix0." }
        }
    }
    for ($try = 1; $try -le 6; $try++) {
        $a = Read-Host '  Copia TUTTA la risposta della chat (il pulsante "copia" del blocco di codice), poi premi Invio qui. Per fermarti scrivi N'
        if ("$a" -match '^[nN]') { Write-Log '  Fermato: l''articolo resta preparato, si riprende dal menu.'; return 'fermato' }
        $ans = Get-Clip
        if ("$ans".Trim()) {
            Set-Content -Path $answerFile -Value $ans -Encoding UTF8
        } else {
            Read-Host "  Negli appunti non c'e' testo: incolla la risposta in $answerFile, salva e premi Invio" | Out-Null
        }
        $r = Invoke-Orpheus @('chat-schede', '--leggi', '--modello', "$Chat", '--articolo', "$Id")
        if (-not $r.Json -or $null -eq $r.Json.lette) { Show-Error $r; return 'errore' }
        $c = @($r.Json.schede | Where-Object { $_ }) | Select-Object -First 1
        if (-not $c) {
            Write-Log '  E'' la stessa risposta gia'' controllata: copia la risposta NUOVA della chat.'
            continue
        }
        if ($c.esito -eq 'accettata') {
            Write-Log ("  ACCETTATA al tentativo {0}: {1} valori con citazione ({2} min). Non e' ancora nel database: alla fine usa 'Invia al database'." -f $c.tentativo, $c.valori_con_citazione, $c.minuti)
            return 'accettata'
        }
        Write-Log ("  RIFIUTATA ({0} problemi: {1})" -f $c.problemi, (@($c.controlli_non_superati) -join ', '))
        $fix = Join-Path $Folder 'correzione.txt'
        if ((Test-Path $fix) -and (Set-Clip (Get-Content -Raw -Encoding UTF8 -Path $fix))) {
            Write-Log '  La correzione e'' negli appunti: incollala nella STESSA chat (Ctrl+V) e inviala.'
        } else {
            Write-Log "  Apri $fix, copia tutto e incollalo nella STESSA chat."
        }
    }
    Write-Log '  Troppi tentativi: lascia stare questo articolo (resta preparato) oppure prova un''altra chat.'
    return 'rifiutata'
}

function Invoke-ChatSchede {
    Write-Log ''
    Write-Log 'SCHEDE CON UNA CHAT GRATUITA (emergenza: pochi articoli o quando Claude non puo'' lavorare)'
    Write-Log '  Solo piani gratuiti: se la chat chiede di pagare, fermati. Mai bozze di manoscritto in una chat esterna.'
    Write-Log '  I file restano in rapporti\schede\chat (questo PC). Il database cambia solo con "Invia al database".'
    $j = Show-ChatState
    if (-not $j) { return }
    $chat = $null
    if ([int]$j.da_leggere -gt 0) {
        Write-Log "  Risposte incollate a mano in risposta.txt e non ancora controllate: $($j.da_leggere)"
        $chat = Select-Chat "$($j.ultimo_modello)" "$($j.cartella)"
        $r = Invoke-Orpheus @('chat-schede', '--leggi', '--modello', "$chat")
        if ($r.Json) { Write-Log ("  Lette {0}: accettate {1}, rifiutate {2}" -f $r.Json.lette, $r.Json.accettate, $r.Json.rifiutate) }
        $j = Show-ChatState
    }
    while ($true) {
        $waiting = @(@($j.articoli) | Where-Object { $_ -and ($_.esito -eq 'aspetta la risposta' -or $_.esito -eq 'rifiutata') })
        Write-Host ''
        Write-Host '  Cosa vuoi fare?'
        Write-Host '    1) Nuovo articolo: lo preparo e ti guido passo per passo'
        if ($waiting.Count -gt 0) { Write-Host "    2) Riprendi un articolo gia' preparato ($($waiting.Count) in attesa o rifiutati)" }
        if ([int]$j.da_inviare -gt 0) { Write-Host "    3) Invia al database le schede accettate ($($j.da_inviare))" }
        Write-Host '    0) Esci'
        $a = "$(Read-Host '  Scrivi il numero e premi Invio')".Trim()
        if ($a -eq '1') {
            if ([int]$j.ancora_da_preparare -le 0) { Write-Log '  Nessun pacchetto aperto da preparare: prima pacchetti.cmd (o il pulsante del cruscotto).'; continue }
            if (-not $chat) { $chat = Select-Chat "$($j.ultimo_modello)" "$($j.cartella)" }
            $p = Invoke-Orpheus @('chat-schede', '--quanti', '1')
            if (-not $p.Json -or $null -eq $p.Json.preparati) { Show-Error $p; continue }
            $art = @($p.Json.preparati | Where-Object { $_ }) | Select-Object -First 1
            Write-Log "  Cartella: $($art.cartella)"
            [void](Invoke-ChatArticolo $art.cartella $art.article_id $chat)
        } elseif ($a -eq '2' -and $waiting.Count -gt 0) {
            for ($i = 0; $i -lt $waiting.Count; $i++) { Write-Host ("    {0}) {1} ({2})" -f ($i + 1), $waiting[$i].article_id, $waiting[$i].esito) }
            $k = 0; $sel = Read-Host '  Numero dell''articolo (solo Invio = 1)'
            if (-not "$sel".Trim()) { $k = 1 } else { [void][int]::TryParse("$sel", [ref]$k) }
            if ($k -lt 1 -or $k -gt $waiting.Count) { continue }
            $w = $waiting[$k - 1]
            if (-not $chat) { $chat = Select-Chat "$($j.ultimo_modello)" "$($j.cartella)" }
            $folder = Join-Path $j.cartella $w.article_id
            $again = Read-Host '  Hai gia'' incollato tutti i messaggi in quella chat? S = si'', riprendo dalla risposta; Invio = ricomincio in una chat nuova'
            [void](Invoke-ChatArticolo $folder $w.article_id $chat -Resume:("$again" -match '^[sS]'))
        } elseif ($a -eq '3' -and [int]$j.da_inviare -gt 0) {
            Invoke-InviaSchedeChat
        } elseif ($a -eq '0' -or -not $a) {
            return
        }
        $j = Show-ChatState
        if (-not $j) { return }
    }
}

function Invoke-InviaSchedeChat {
    Write-Log ''
    Write-Log 'INVIO DELLE SCHEDE DELLA CHAT GRATUITA (stessi controlli delle schede di Claude; prima una copia di sicurezza)'
    $r = Invoke-Orpheus @('chat-schede', '--invia')
    if (-not $r.Json -or $null -eq $r.Json.schede_trovate) {
        Show-Error $r
        if ($r.Raw -match 'migrate') { Write-Log "  Fai prima doppio clic su $Root\installa_orpheus.cmd: aggiorna il database alla nuova versione." }
        return
    }
    $j = $r.Json
    if ($j.nota) { Write-Log "  $($j.nota)"; return }
    if ($j.backup_prima_della_scrittura) { Write-Log "  Copia di sicurezza: $($j.backup_prima_della_scrittura)" }
    Write-Log ("  Schede trovate: {0}  -  nel database adesso {1}, c'erano gia' {2}, rifiutate {3}" -f $j.schede_trovate, $j.inviate, $j.gia_nel_database, $j.rifiutate)
    foreach ($s in @($j.schede)) {
        if (-not $s) { continue }
        $why = if (@($s.controlli_non_superati).Count -gt 0) { " (" + (@($s.controlli_non_superati) -join ', ') + ")" } else { '' }
        Write-Log "    - $($s.article_id): $($s.esito)$why"
    }
    Write-Log ("  Nel database: schede {0}, valori {1}" -f $j.stato.schede_accettate, $j.stato.valori)
    if ([int]$j.rifiutate -gt 0) { Write-Log '  I motivi sono in invio_rifiutato.txt nella cartella di ogni articolo (solo su questo PC).' }
    if ($j.verifica_copia) { Write-Log "  ATTENZIONE: $($j.verifica_copia.messaggio)" }
}

function Invoke-NuovoArticolo {
    Write-Log ''
    Write-Log 'NUOVO ARTICOLO DAI TUOI DATI (una sezione alla volta; i file restano su questo PC, nel registro solo gli stati)'
    $r = Invoke-Orpheus @('nuovo-articolo', '--elenco')
    if (-not $r.Json -or -not $r.Json.cartella) { Show-Error $r; return }
    $base = $r.Json.cartella
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    $projects = @(Get-ChildItem -LiteralPath $base -Directory | Sort-Object LastWriteTime -Descending)
    Write-Host ''
    if ($projects.Count -gt 0) {
        Write-Host 'Articoli in preparazione (il piu'' recente per primo):'
        for ($i = 0; $i -lt $projects.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $projects[$i].Name) }
        $ans = Read-Host 'Numero dell''articolo, oppure N per uno nuovo (solo Invio = 1)'
    } else {
        $ans = 'N'
    }
    if ($ans -and $ans.Trim().ToUpper() -eq 'N') {
        $name = Read-Host 'Nome breve del nuovo articolo (lettere, numeri, - e _; es. basket-cod)'
        if (-not $name -or $name.Trim() -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$') { Write-Log '  Nome non valido: rilancia nuovo_articolo.cmd.'; return }
        $name = $name.Trim()
        $rv = Invoke-Orpheus @('manoscritto', '--riviste')
        $journals = @(@($rv.Json.riviste) | Where-Object { $_ })
        Write-Host 'Riviste con la scheda:'
        for ($i = 0; $i -lt $journals.Count; $i++) { Write-Host ("  {0}. {1} - {2}" -f ($i + 1), $journals[$i].sigla, $journals[$i].titolo) }
        $k = 0
        $ans = Read-Host 'Numero della rivista'
        if (-not [int]::TryParse(("" + $ans).Trim(), [ref]$k) -or $k -lt 1 -or $k -gt $journals.Count) { Write-Log '  Numero non valido: rilancia nuovo_articolo.cmd.'; return }
        $ans = Read-Host 'Tipo: 1 = studio originale, 2 = revisione sistematica o meta-analisi, 3 = protocollo (solo Invio = 1)'
        $tipo = 'originale'
        if ($ans -and $ans.Trim() -eq '2') { $tipo = 'revisione' } elseif ($ans -and $ans.Trim() -eq '3') { $tipo = 'protocollo' }
        $env:ORPHEUS_PROGETTO = $name
        try { $r = Invoke-Orpheus @('nuovo-articolo', '--rivista', $journals[$k - 1].sigla, '--tipo', $tipo) } finally { Remove-Item Env:ORPHEUS_PROGETTO -ErrorAction SilentlyContinue }
        if (-not $r.Json -or -not $r.Json.creato) { Show-Error $r; return }
        Write-Log "  Creato un articolo nuovo per $($journals[$k - 1].sigla) (tipo: $tipo)."
        Write-Log ("  Compila {0} (in INGLESE, si aprono con Excel; salvali come CSV), poi rilancia nuovo_articolo.cmd." -f (@($r.Json.da_compilare) -join ', '))
        try { Start-Process explorer.exe (Join-Path $base $name) } catch { }
        return
    }
    $pick = 1
    if ($ans -and $ans.Trim()) {
        if (-not [int]::TryParse($ans.Trim(), [ref]$pick) -or $pick -lt 1 -or $pick -gt $projects.Count) { Write-Log '  Numero non valido: rilancia nuovo_articolo.cmd.'; return }
    }
    $name = $projects[$pick - 1].Name
    $env:ORPHEUS_PROGETTO = $name
    try {
        $r = Invoke-Orpheus @('nuovo-articolo')
        if (-not $r.Json -or $null -eq $r.Json.pronto) { Show-Error $r; return }
        if (-not $r.Json.pronto) {
            Write-Log '  Da sistemare nei file del progetto:'
            foreach ($p in @($r.Json.problemi)) { if ($p) { Write-Log "    - $p" } }
            Write-Log '  Correggi e rilancia nuovo_articolo.cmd.'
            try { Start-Process explorer.exe (Join-Path $base $name) } catch { }
            return
        }
        $j = $r.Json
        Write-Log ("  Rivista {0}; evidenze da ORPHEUS: {1} articoli (ricerca {2}); risultati: {3} righe" -f $j.rivista, $j.evidenze, $j.ricerca_evidenze, $j.risultati)
        foreach ($s in @($j.inviate)) { if ($s) { Write-Log ("  Sezione {0}: {1}{2}" -f $s.sezione, $s.esito, $(if ($s.esito -ne 'accettabile') { " ($($s.problemi) problemi: vedi sezioni\da_inviare\$($s.sezione).motivi.txt)" } else { '' })) } }
        Write-Log '  Sezioni:'
        foreach ($prop in $j.sezioni.PSObject.Properties) { Write-Log ("    {0,-13} {1}" -f $prop.Name, $prop.Value) }
        $preview = Join-Path (Join-Path $base $name) 'anteprima.html'
        if (Test-Path $preview) { try { Start-Process $preview } catch { } }
        $todo = @($j.sezioni.PSObject.Properties | Where-Object { $_.Value -eq 'da approvare' } | Select-Object -First 1)
        if ($todo.Count -gt 0) {
            $sec = $todo[0].Name
            $ans = Read-Host "Hai letto l'anteprima: approvi la sezione $sec? Scrivi S e premi Invio (altrimenti solo Invio)"
            if ($ans -and $ans.Trim().ToUpper() -eq 'S') {
                $a = Invoke-Orpheus @('nuovo-articolo', '--approva', $sec)
                if ($a.Json -and $a.Json.approvata) { Write-Log "  Sezione $sec approvata. Prossima: $($a.Json.prossima)" } else { Show-Error $a }
            } else {
                Write-Log "  Sezione $sec non approvata: scrivi a Claude in sessione cosa cambiare."
            }
        } elseif ($j.prossima) {
            Write-Log "  Prossima sezione: $($j.prossima). Scrivi a Claude, in una sessione con il PC collegato: scrivi la sezione $($j.prossima)."
        } else {
            Write-Log '  Tutte le sezioni sono approvate: preparo la bozza in Word (bozza.docx) nel formato della rivista.'
            $b = Invoke-Orpheus @('nuovo-articolo', '--bozza')
            if (-not $b.Json -or -not $b.Json.bozza) { Show-Error $b; return }
            $z = $b.Json.bozza
            Write-Log ("  Bozza scritta: {0} riferimenti ({1}), {2} tabelle, testo {3} parole, abstract {4} parole" -f $z.riferimenti, $z.stile, $z.tabelle, $z.parole_testo, $z.parole_abstract)
            Write-Log ("  Formato: {0}, {1} pt, interlinea {2}, margini {3} cm (dalla scheda: {4})" -f $z.formato.carattere, $z.formato.corpo_pt, $z.formato.interlinea, $z.formato.margine_cm, ((@($z.formato.da_scheda) | Where-Object { $_ }) -join ', '))
            if ($z.gate.superato) {
                Write-Log '  Gate superato: citazioni tutte verificate, sezioni uguali a quelle approvate.'
            } else {
                Write-Log ("  Gate NON superato: citazioni non verificate {0}, ritrattati {1}, sezioni cambiate dopo l'approvazione: {2}" -f $z.gate.citazioni_non_verificate, $z.gate.ritrattati, ((@($z.gate.sezioni_cambiate_dopo_l_approvazione) | Where-Object { $_ }) -join ', '))
            }
            if ($z.controllo -and $z.controllo.conteggi) {
                $c = $z.controllo.conteggi
                Write-Log ("  Controllo della bozza ({0}): da correggere {1}, da guardare {2}, conforme {3}" -f $z.controllo.rivista, $c.'da correggere', $c.'da guardare', $c.conforme)
                $rep = Join-Path (Join-Path $base $name) 'bozza_controllo.html'
                if (Test-Path $rep) { try { Start-Process $rep } catch { } }
            }
            Write-Log '  Apri bozza.docx in Word: aggiungi autori e affiliazioni (pagina del titolo), controlla e completa.'
            try { Start-Process explorer.exe (Join-Path $base $name) } catch { }
        }
    } finally { Remove-Item Env:ORPHEUS_PROGETTO -ErrorAction SilentlyContinue }
}

function Invoke-Cruscotto {
    Write-Log ''
    Write-Log 'CRUSCOTTO: si apre nel browser una pagina servita da questo PC (indirizzo 127.0.0.1, i dati restano qui).'
    Write-Log 'Lascia aperta questa finestra mentre usi il cruscotto; chiudila per fermarlo.'
    Write-Log ''
    $env:PYTHONUTF8 = '1'
    & $VenvPy -m orpheus cruscotto --comandi (Join-Path $Root 'orpheus_comandi.ps1')
    if ($LASTEXITCODE -ne 0) {
        Write-Log "  Il cruscotto non e' partito (codice $LASTEXITCODE): leggi il messaggio qui sopra."
        Write-Log "  Se mancano i pacchetti, fai doppio clic su $Root\installa_orpheus.cmd e riprova."
    }
}

switch ($Comando) {
    'leggi'     { Invoke-Leggi }
    'cerca'     { Invoke-Cerca }
    'controllo' { Invoke-Controllo }
    'verifica'  { Invoke-Verifica }
    'verifica_pubmed' { Invoke-VerificaPubmed }
    'benchmark' { Invoke-Benchmark }
    'doppioni'  { Invoke-Doppioni }
    'autori'    { Invoke-Autori }
    'europepmc' { Invoke-EuropePMC }
    'riferimenti' { Invoke-Riferimenti }
    'verifica_citazioni' { Invoke-VerificaCitazioni }
    'pacchetti' { Invoke-Pacchetti }
    'schede'    { Invoke-Schede }
    'controllo_schede' { Invoke-ControlloSchede }
    'report'    { Invoke-Report }
    'prova_ripristino' { Invoke-ProvaRipristino }
    'riviste'   { Invoke-Riviste }
    'schede_riviste' { Invoke-SchedeRiviste }
    'metriche_riviste' { Invoke-MetricheRiviste }
    'semantica' { Invoke-Semantica }
    'benchmark_ricerca' { Invoke-BenchmarkRicerca }
    'affermazioni' { Invoke-Affermazioni }
    'lavora'    { Invoke-Lavora }
    'cruscotto' { Invoke-Cruscotto }
    'manoscritto' { Invoke-Manoscritto }
    'revisione_sistematica' { Invoke-RevisioneSistematica }
    'scheda_unica' { Invoke-SchedaUnica }
    'nuovo_articolo' { Invoke-NuovoArticolo }
    'schede_locali' { Invoke-SchedeLocali }
    'chat_schede' { Invoke-ChatSchede }
    'invia_schede_chat' { Invoke-InviaSchedeChat }
    'revisione' { Invoke-Revisione }
    'validazione' { Invoke-Validazione }
    'aggiorna_articoli' { Invoke-Lavora 'articoli' }
    'aggiorna_riviste' { Invoke-Lavora 'riviste' }
    'invia_lavoro_claude' { Invoke-Lavora 'conoscenza' }
    'manutenzione' { Invoke-Manutenzione }
    'prepara_manoscritto' { Invoke-PreparaManoscritto }
    'lettera' { Invoke-Lettera }
    'risposta' { Invoke-Risposta }
    'esporta_schede' { Invoke-EsportaSchede }
    'importa_schede' { Invoke-ImportaSchede }
}
Write-Log ''
Write-Log "Registro: $LogFile"
# Copia del registro per il loop di Claude nel cloud (ramo registri-pc su GitHub; vedi pubblica_registro.ps1).
$Pub = Join-Path $Root 'pubblica_registro.ps1'
if (Test-Path $Pub) { & $Pub -File $LogFile }
