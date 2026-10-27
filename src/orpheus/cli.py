"""Command-line entry point: `orpheus <command>` or `python -m orpheus <command>`.

Data
  init                 create a NEW, empty knowledge base and the data-folder structure
  migrate              upgrade an existing database after `git pull`
  ingest-inbox         read the standard inbox (inbox/riviste/<sigla>/…, inbox/articoli_vari)
  ingest-pdf PATH…     read specific PDF files or folders (--kind article|author_guidelines|other)
  search TEXT…         full-text search in the pages of the PDFs read so far (--documents: one line per PDF);
                       only complete articles and guidelines (content.py), --tutti: also abstracts and single pages
  report               status page rapporti/report_<data>.html (read-only): documents, coverage per journal folder,
                       DOI/PubMed/Europe PMC checks, citations, cards and the last hand check; every number with its
                       table and date
  openalex             OpenAlex for the confirmed DOIs (READ-ONLY, nothing written): OpenAlex id, journal ISSN-L,
                       open access, citations, references. The source is disabled until the user decides;
                       --prova: no network (how many DOIs and requests); --limit N
  riviste              journal catalogue from the saved Crossref answers (read-only): title, ISSN, publisher, articles
  quality-report       check the PDFs read so far (text, DOI, journal, abstracts, scans, doubles, DOI verification);
                       writes rapporti/qualita_<data>.csv in the data folder (read-only on the database)
  verify-dois          verify the DOI candidates against Crossref (needs ORPHEUS_CONTACT_EMAIL and the internet);
                       an article is created only when the record matches the PDF. Already verified DOIs are
                       not asked again. --ricontrolla: ask again for "non trovato"/"non corrisponde";
                       --rivaluta: no network, apply the current rules to the stored answers; --limit N.
                       First the DOIs of the PDFs not yet confirmed are read again from the stored text
                       (Elsevier DOIs with the last digits split off)
  scrivi-riviste       journal / journal_issn rows for the journals of riviste_prioritarie.csv, checked against the
                       saved Crossref answers (title, publisher, ISSN types), and links the confirmed articles and
                       their PDFs to their journal (Q9, rules journals-v2: an ISSN type 'unknown' is completed when
                       the corrected list gives it); --prova: write nothing
  sistema-riviste      T-054: siglas that are not valid folder names renamed in riviste_prioritarie.csv and in the
                       folders (A&R → AR), a folder without a row whose articles are all of ONE journal merged into
                       that journal's folder (PIIS → JP), tracking parameters removed from the URLs, missing folders
                       created; copy of the list in data/archive, registro in rapporti/spostamenti; --prova
  riconosci-riviste    T-054: a journal of confirmed articles (Crossref) that is not in the list becomes a new row
                       (sigla from the initials, title, publisher, ISSNs; site from OpenAlex), then scrivi-riviste and
                       its folders; --prova, --senza-openalex
  sposta-articoli      T-054: the PDFs of articoli_vari whose article has a journal go to riviste/<sigla>/articoli
                       (same file already there → inbox/doppioni; another file with that name → _2); registro in
                       rapporti/spostamenti; the others are listed "da assegnare"; --prova; --annulla REGISTRO|ultimo
  metriche-riviste     official metrics of the journals (T-028, rules metrics-v1) into journal_metric: SJR, quartiles,
                       h-index and citations per document from the SCImago file in inbox/metriche (scimagojr <anno>.csv,
                       downloaded by the user) and 2-year mean citedness, h-index and id from OpenAlex (/sources/issn:,
                       network); a year of SCImago is never rewritten; --prova: no network, write nothing
  linee-guida          dated versions of the author guidelines already read (journal_policy_version, rules policy-v2):
                       a version = the set of the journal's guideline PDFs still in the folder (one per printed page
                       address, the newest print of each page); fingerprint of the text without the browser's print
                       lines; a new row only when the set or a text changes (then "da rileggere"); --prova: write nothing.
                       "avvisi" (T-029, policy-diff-v1): journals whose guidelines changed after their card (card to
                       redo), with the sentences removed/added and the card values whose quote is no longer there
  cerca-doi            DOIs that the PDF does not print (JSCR) or prints but Crossref does not know (FAS PII DOIs):
                       one Crossref search per complete article of a listed journal (title + printed volume/issue/
                       first page, rules search-v3), then the DOI found is verified like verify-dois; --ricontrolla,
                       --limit N
  verifica-pubmed      look up the confirmed full-text articles in PubMed (NCBI E-utilities, free, no key; needs
                       ORPHEUS_CONTACT_EMAIL and the internet): PMID, MeSH and publication types only when the PubMed
                       record has the article's DOI. Already checked articles are not asked again. --ricontrolla: ask
                       again for "non trovato"/"discordante"/"ambiguo"; --prova: no network, no writes (what would be
                       asked, requests and minutes); --limit N
  europepmc            look up the confirmed full-text articles in Europe PMC (free, no key; needs ORPHEUS_CONTACT_EMAIL
                       and the internet): open-access full text, licence, sections and references (JATS read in
                       memory). Without --salva READ-ONLY: nothing is written; rapporti/europepmc_<data>.csv.
                       --salva (migration 0006, user's yes): backup first, then one fulltext_check per article,
                       PMCID and licence, the JATS text and its sections only with a Creative Commons licence;
                       articles already checked are skipped (--ricontrolla: ask again not found / discordant /
                       ambiguous). --prova: no network (articles to ask, minimum requests, minutes); --limit N
  riferimenti          reference lists of the complete articles from the Crossref answers already stored (no network):
                       how many with DOI, which cited DOIs are already in ORPHEUS (citations inside the corpus);
                       rapporti/riferimenti_<data>.csv; READ-ONLY, nothing is verified or written (T-014 parte 1)
  verifica-citazioni   check the reference lists of the complete articles against Crossref (rules cite-v2, ADR-0004:
                       year, first author, title), groups of up to 50 DOIs per request; one citation row per
                       reference (verified / mismatch / not_found / unverified); backup first; articles already done
                       are skipped. Needs ORPHEUS_CONTACT_EMAIL and the internet. --prova: no network, no writes; --limit N;
                       --rivaluta: no network, apply the current rules again to the rows already written (stored
                       Crossref answers + Europe PMC texts kept since), backup first
  allinea-accesso      article.access_level from the content of its PDFs: abstract_only when all are conference
                       abstracts or single pages, full_text otherwise (decision A of 2026-10-11; also run by verify-dois)
  doppioni             possible duplicates (rules dedup-v1: same article in more files, versions from Crossref, same
                       title + year + first author) and the proposed author rows from the stored Crossref answers;
                       rapporti/doppioni_<data>.csv; read-only, nothing is merged or written
  scrivi-autori        write the authors-v1 proposal into author / article_author (backup first, one transaction,
                       audit event; articles that already have authors are skipped, so it can be run again)
  ocr PDF... [--lingua eng]  OPTIONAL OCR of scanned PDFs (T-020): free Tesseract if installed, otherwise "saltato";
                       text to rapporti/ocr/<nome>.txt, labelled as OCR; read-only, the database is not opened
  separa-non-completi  move the PDFs that are not complete articles (conference abstracts, 1-2 page items: content.py)
                       from the inbox to non_completi/<abstract_congresso|pagina_singola>; the database is not changed
  benchmark-csv        file for the human benchmark (T-012): 50 COMPLETE articles to check by hand against the
                       PDFs, rapporti/benchmark_articoli_completi_50_<data>.csv (Excel); deterministic, read-only
  benchmark-risultati [FILE]  precision of the metadata from the file filled in by hand (default: the newest
                       rapporti/benchmark_articoli_completi_*.csv saved under another name); read-only
Article cards (T-019, ADR-0008; Claude fills them in during a session, ORPHEUS accepts them only through the validators)
  pacchetti [--articoli ID…]  work packets of the first lot (10 open-access texts + 20 drawn with a fixed seed) or of the
                       given articles: one task + workflow_run (executor claude_session) each and
                       rapporti/pacchetti/<article_id>.json; articles with an accepted card are skipped; can be run again.
                       --modo rivista|equo|casuale [--rivista SIGLA] [--quanti N]: N new packets by priority (one
                       journal; the same number per journal in turn; the order of the draw)
  submit [FILE…] [--dal-cloud]  (--dal-cloud: first copy the cards the hourly loop wrote in origin/main:schede_cloud/)
                       check the cards in rapporti/schede/da_inviare (or the given files) against the text in the
                       database (rules cards-v1): validation_result always; the card only if every check passes.
                       Backup first; accepted files move to schede/accettate, refused ones get <name>.motivi.txt
  controlla-scheda SCHEDA PACCHETTO  the same content checks on the packet's text, no database (for Claude)
  schede-locali [--modello M] [--quanti N]  pilot (ADR-0010): cards of the open packets written by a free local model
                       (Ollama on 127.0.0.1:11434 or OLLAMA_HOST; answers streamed and capped by --max-token, a
                       failed piece is skipped; progress lines on stderr) into rapporti/schede/locali, with the
                       checks of controlla-scheda;
                       nothing goes into the database (hand check first)
  chat-schede [--quanti N] [--leggi --modello M] [--stato]  pilot (ADR-0011, executor chat_manuale): messages to
                       paste into a free chat for the next open packet(s), in rapporti/schede/chat/<article_id>/;
                       --leggi reads every pasted risposta.txt and checks it with the validators of submit
                       (accepted -> scheda.json, refused -> reasons and correzione.txt); nothing goes into the database.
                       --invia: the accepted cards into the database (checks of submit, backup first, executor
                       chat_manuale); emergency route, Claude in session stays the main one (decision of 2026-10-17)
  schede-csv           file for the hand check: 10 accepted cards drawn with a fixed seed, one row per value (read-only)
  schede-riviste       journal cards (T-027, journal-cards-v1) in rapporti/schede_riviste/da_inviare: checked against the
                       current guideline version; written only if every check passes (accepted -> accettate, refused ->
                       <name>.motivi.txt)
  schede-riviste-controllo  hand check of all the current journal cards: the CSV to fill in, or — when a filled copy is
                       there — its precision, and the S/N written into journal_card_value
  controlla-scheda-rivista FILE  the checks of schede-riviste on one card, nothing written (for Claude)
  schede-risultati [FILE]  precision of the values from the filled file (read-only)
Search by meaning (Fase 3, T-030..T-032; user's decisions of 2026-10-15: local multilingual model, migration 0009)
  segmenta [--prova]   pieces of text (~1 paragraph, with pages and section; no bibliography, no running heads) of the
                       complete articles into `segment` (rules segment-v1); unchanged articles are left as they are
  vettori [--prova] [--solo-scarica]  vectors of the segments with intfloat/multilingual-e5-small (ONNX, CPU); the
                       model (~0.5 GB) is downloaded once into this PC's local folder (modelli/); resumable
  cerca-testi DOMANDA... [--modo ibrida|parole|significato] [--limit N]  search in the segments, question in Italian or
                       in English; one line per article with page, section and passage (hybrid-v2)
  benchmark-ricerca [FILE]  recall@5/@10, hit@10, MRR of pagine (baseline), parole, significato, ibrida on the
                       questions checked by the user (rapporti/benchmark_ricerca_*_compilato.csv); read-only
Claims and evidence (Fase 3, T-033; user's decisions of 2026-10-15: from the 30 cards, simple level, migration 0010)
  affermazioni         claims files in rapporti/affermazioni/da_inviare (claims-v1, written by Claude, in Italian or in
                       English: "lingua" it/en, one file per article and language): checked against the article's PDF
                       (language, page, verbatim quote, numbers); written with the level of evidence (appraisal-v1)
                       only if every check passes (accepted -> accettate, refused -> <name>.motivi.txt)
  affermazioni-controllo  hand check of the claims: a filled copy with new S/N -> its precision and the S/N written into
                       scientific_claim / evidence_record; else the CSV of the claims never checked (e.g. new English
                       claims); else the latest results
  controlla-affermazioni FILE  the checks of `affermazioni` on one file, nothing written (for Claude)
The dashboard (Fase 5, T-036; user's decisions of 2026-10-15: interactive, can start lavora)
  cruscotto [--porta N] [--senza-browser] [--comandi PS1]  the dashboard in the browser, served by this PC on 127.0.0.1
                       (dashboard-v1): overview, articles, claims, search, journals, lavora, system; read-only except
                       the button "Avvia lavora" (the same lavora as lavora.cmd); stops when the window is closed
Manuscripts (Fase 6, T-037; user's decisions of 2026-10-15: Word .docx, only on the PC and in sessions with Claude,
the check first, original studies and systematic reviews / meta-analyses)
  manoscritto [FILE] [--rivista SIGLA ...|tutte] [--tipo auto|originale|revisione|protocollo] [--verifica-riferimenti]  a .docx manuscript (in
                       manoscritti/ of the data folder; FILE may also come from ORPHEUS_MANOSCRITTO) checked against the
                       current journal cards (compliance-v1): length, abstract, keywords, sections, figures and tables,
                       references and citations, statements, page format; report in rapporti/manoscritti (stays on the
                       PC); the output has numbers and outcomes only, never the text; read-only on the database
  manoscritto --riviste  the journals with a card (for manoscritto.cmd)
  revisione [FILE] --rivista SIGLA [--tipo]  simulated peer review (T-040, review-v1): packet for a session with Claude
                       in rapporti/manoscritti/revisione/<impronta>_<sigla>/ (paragraphs numbered, checks of the
                       manuscript for that journal, three profiles); a da_inviare/revisione.json written by Claude
                       is checked (quotes in the paragraph, bases, every check to fix discussed, no prediction) and
                       becomes revisione.html; output: counts only
  revisione --controlla REV --pacchetto P  the same checks, for Claude
  scheda-unica --converti | --esporta [ART|--tutte] | --correggi ART CAMPO (--valore V [--citazione Q --pagina P]
                       | --conferma | --non-riportato) [--ordine N] [--nota T]  EURYDIKE E2 (T-051c, cards-v2, D16): the
                       unified article card shared by ORPHEUS and EURYDIKE; --converti copies the cards-v1 cards (never
                       changed; backup first); --correggi = the user's decision on one value (a new row supersedes the
                       old one, the quote is looked for in the PDF); --esporta writes rapporti/schede_v2/<ART>.json + .csv
                       with the values corrected by hand marked
  revisione-sistematica [FILE] [--cartella]  EURYDIKE E1 (T-051b, review-audit-v1): a systematic review or meta-analysis
                       (.docx) checked for internal consistency without a journal: PRISMA flow that adds up (text,
                       legends and a diagram drawn in Word), abstract = flow, rows of the table of studies = included,
                       k and n of the pooled analyses, CIs in square brackets, I² and τ², registration, risk-of-bias tool,
                       conversions from medians; plus the PRISMA 2020 checklist and the numbers; report in
                       rapporti/manoscritti; output: numbers and outcomes only; no database needed
  validazione [FILE] --rivista SIGLA  Fase 7 (validation-v1): control file for the hand check of references,
                       compliance, numbers and simulated review of ONE manuscript, then precision with Wilson 95% CI
                       against the thresholds; files in rapporti/manoscritti/validazione/; output: counts only
  lettera [FILE] --rivista SIGLA   cover letter (cover-v1) for a session with Claude, in rapporti/manoscritti/lettera/:
                       title, journal, type, originality, approval, conflicts, registration, numbers from the
                       manuscript, 150-600 words; accepted -> lettera.docx (--controlla/--pacchetto for Claude)
  risposta [FILE] --rivista SIGLA  response to reviewers (response-v1) from commenti_revisori.txt and the REVISED
                       manuscript, in rapporti/manoscritti/risposta/: every comment answered once, changes quoted
                       from the revised paragraph; accepted -> risposta_revisori.docx (--controlla/--pacchetto)
  nuovo-articolo [NOME] [--rivista SIGLA] [--approva SEZIONE]  a new article from the user's data (T-041, writing-v1):
                       creates nuovi_articoli/NOME with studio.csv and risultati.csv; then checks them, writes
                       pacchetto.json (study, results, journal rules, evidence from ORPHEUS), checks the sections in
                       sezioni/da_inviare (citations verified, numbers traced) and writes anteprima.html; --approva
                       records the user's approval; read-only on the database
  nuovo-articolo --controlla SEZIONE.json --pacchetto PACCHETTO.json  the checks on one section (for Claude)
  nuovo-articolo [NOME] --bozza  the Word draft when every section is approved (T-041 part 2, draft-v1): bozza.docx in
                       the format of the journal card, numbered references from the verified metadata, tables from
                       risultati.csv; gate (citations verified, sections unchanged) and the manuscript check
                       (bozza_controllo.html); exit 4 when the gate is not passed; counts only in the output
The queue of the work (Fase 4, T-035; user's decisions of 2026-10-15: one double click, migration 0011)
  lavora               every step in order (engine-v1: read, DOIs, journals, PubMed, Europe PMC, citations, authors,
                       cards, journal cards, segments, vectors, claims, report), each only when something is missing;
                       task / workflow_run for each step run; retries; a run left unfinished is resumed
  lavora --piano [--conti]  read-only: order of the steps, a run in progress or interrupted, with --conti what is
                       missing for each step, the work for Claude and for the user
  lavora --passo PASSO --giro G [--pid N]  one step (used by lavora.cmd, one call per step; N: the process that owns the
                       run, the PowerShell window)
  lavora --fine --giro G  end of the run: what was done, the work for Claude and for the user; the lock is released
Safety and moving data between PCs (ADR-0007)
  backup               copy of the database into this PC's local backup folder
  prova-ripristino [--backup FILE]  restore the newest local backup into a temporary folder and check it
                       (sha256, integrity, audit chain, migrations, identity, position, table counts); read-only
  salute               read-only: the health of ORPHEUS on this PC (health-v1): last runs of lavora and errors, age of
                       the backups and of the restore test, disk space, migrations, paid services; exit 2 on errore
  export DIR           one-file bundle (database + PDF archive) to carry to another PC
  import BUNDLE        bring a bundle in; refuses older, divergent or foreign data
  accept-state --yes   declare the database currently open as the valid one on this PC
Checks
  doctor, status, audit-verify, integrity, data-dictionary

Every command that opens the database first compares it with what this PC remembers
(lineage.check). An older or divergent copy can be read, but not changed.

Exit codes: 0 ok · 1 doctor found problems · 2 integrity/audit problem · 3 some files not read
            4 blocked for safety · 5 setup or usage error
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import (audit, ocr, backups, benchmark, card_store, claims, config, content, crossref, db, dedup, doi_search, engine, europepmc,
               europepmc_survey, journal_metrics, journal_table,
               journal_cards, journals, layout, lineage, openalex, policy_changes, policy_versions,
               pubmed, quality, references, report, search_benchmark, segments, semantic, transfer, verify_citations,
               verify_doi, verify_pubmed)
from .config import check_budget_policy, load_models, load_project, load_secrets, load_sources
from .ingest_pdf import ingest_paths, ingest_pdf, search_documents, search_pages
from .localstate import LocalState
from .sources import sync_sources


class CliError(Exception):
    def __init__(self, message: str, code: int = 5, **details):
        super().__init__(message)
        self.message, self.code, self.details = message, code, details


_OPEN: list[sqlite3.Connection] = []


def _connect(path: Path) -> sqlite3.Connection:
    """Every connection opened by a command is closed when the command ends (main), so no file
    handle is left behind — on Windows an open handle would block replacing the database."""
    conn = db.connect(path)
    _OPEN.append(conn)
    return conn


def _close_all() -> None:
    while _OPEN:
        try:
            _OPEN.pop().close()
        except sqlite3.Error:
            pass


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


@dataclass
class Ctx:
    conn: sqlite3.Connection
    project: config.ProjectConfig
    home: Path
    db_path: Path
    state: LocalState
    report: lineage.Report
    head_before: lineage.Head


def _settings():
    project = load_project()
    home = config.home()
    return project, home, home / project.storage.db_path, LocalState(config.local_dir())


MISSING_DB = ("Database non trovato in {path}. Se i dati sono sul disco esterno, controlla che sia collegato e che "
              "ORPHEUS_HOME nel file .env indichi la cartella giusta. Per creare un database NUOVO e vuoto: `orpheus init`.")


def _open(*, write: bool, allow_pending: bool = False) -> Ctx:
    project, home, db_path, state = _settings()
    if not db_path.exists():
        raise CliError(MISSING_DB.format(path=db_path))
    conn = _connect(db_path)
    unknown = db.unknown_applied(conn)
    if unknown:
        raise CliError(f"Il database è stato aggiornato da una versione più recente di ORPHEUS (migrazioni {', '.join(unknown)}). "
                       "Su questo PC esegui `git pull` e reinstalla prima di usarlo.")
    states = db.status(conn)
    if any(m["state"] == "MODIFIED" for m in states):
        raise CliError("Una migrazione già applicata risulta modificata nel codice: non usare questo codice (vedi `orpheus status`).", code=2)
    pending = [m["version"] for m in states if m["state"] == "pending"]
    if pending and not allow_pending:
        raise CliError(f"Il codice è più recente del database (migrazioni da applicare: {', '.join(pending)}). Esegui `orpheus migrate`.")
    report = lineage.check(conn, state)
    if report.blocking:
        if write:
            raise CliError(report.message, code=4, verifica_copia=report.as_dict())
        print(report.message, file=sys.stderr)
    return Ctx(conn, project, home, db_path, state, report, lineage.head(conn))


def _finish(ctx: Ctx, *, wrote: bool) -> dict:
    """Remember the state this PC has now seen; back up automatically after changes."""
    out: dict = {}
    if ctx.report.blocking:
        out["verifica_copia"] = ctx.report.as_dict()
        return out
    h = lineage.head(ctx.conn)
    if h.database_id:
        ctx.state.record_seen(h, ctx.db_path)
    if wrote:
        entry = backups.maybe_auto_backup(ctx.conn, ctx.state, ctx.project.local, head_before=ctx.head_before)
        if entry:
            out["backup_automatico"] = str(ctx.state.backup_dir / entry["file"])
    if ctx.report.status in ("first_time", "no_identity"):
        out["nota"] = ctx.report.message
    return out


def _locator(ctx: Ctx) -> layout.PdfLocator:
    """T-054: finds the PDFs where they are now (moved files), fingerprints kept in this PC's local folder."""
    return layout.PdfLocator(ctx.home, ctx.project.storage.inbox_dir, ctx.state.root)


# ---------------------------------------------------------------- data commands

def cmd_init(args) -> int:
    project, home, db_path, state = _settings()
    if db_path.exists():
        raise CliError(f"Esiste già un database in {db_path}: `init` crea solo database nuovi. "
                       "Per aggiornarlo dopo `git pull` usa `orpheus migrate`.")
    known = state.databases()
    if known and not args.yes:
        listing = "; ".join(f"id {k[:8]}… visto in {v.get('db_path')} il {(v.get('seen_at') or '')[:10]}" for k, v in known.items())
        raise CliError(f"Questo PC conosce già un database ({listing}). Se il disco esterno non è collegato o ORPHEUS_HOME "
                       "nel file .env è sbagliata, correggi prima quello. Per creare comunque un database NUOVO aggiungi --yes.", code=4)
    created = layout.create_data_home(home, project.storage)
    conn = _connect(db_path)
    applied = db.migrate(conn)
    synced = sync_sources(conn)
    audit.record(conn, actor="cli", action="init", payload={"applied": applied, "sources_synced": synced})
    h = lineage.head(conn)
    state.record_seen(h, db_path)
    out = {"database": str(db_path), "database_id": h.database_id, "migrazioni": applied, "creati": created,
           "cartella_locale": str(state.root)}
    warnings = config.path_warnings(home, state.root, home_configured=config.home_is_configured())
    if warnings:
        out["avvisi"] = warnings
    _print(out)
    return 0


def cmd_migrate(_args) -> int:
    ctx = _open(write=True, allow_pending=True)
    before = None
    if any(m["state"] == "pending" for m in db.status(ctx.conn)) and lineage.head(ctx.conn).database_id:
        # A copy of the database as it was, on this PC's disk, before its structure changes.
        entry = backups.make_backup(ctx.conn, ctx.state, reason="before_migrate")
        before = str(ctx.state.backup_dir / entry["file"])
    applied = db.migrate(ctx.conn)
    synced = sync_sources(ctx.conn)
    if applied or synced:
        audit.record(ctx.conn, actor="cli", action="migrate", payload={"applied": applied, "sources_synced": synced})
    out = {"migrazioni_applicate": applied, "fonti_aggiornate": synced}
    if before:
        out["backup_prima_della_migrazione"] = before
    _print({**out, **_finish(ctx, wrote=True)})
    return 0


def _summarise(results) -> tuple[dict, list[dict]]:
    summary = {"ingested": 0, "duplicate": 0, "error": 0}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1     # "completed": a document received from a colleague
    files = [{k: (str(v) if k == "path" else v) for k, v in r.__dict__.items() if v not in (None, [], 0)} for r in results]
    return summary, files


def cmd_ingest_pdf(args) -> int:
    ctx = _open(write=True)
    archive = None if args.no_archive else ctx.home / ctx.project.storage.archive_dir
    results = ingest_paths(ctx.conn, args.paths, kind=args.kind, archive_dir=archive, home=ctx.home)
    summary, files = _summarise(results)
    _print({"summary": summary, "files": files, **_finish(ctx, wrote=True)})
    return 0 if summary["error"] == 0 else 3


def cmd_ingest_inbox(_args) -> int:
    ctx = _open(write=True)
    storage = ctx.project.storage
    journals, problems = layout.read_journal_list(ctx.home / storage.journals_csv)
    created = layout.ensure_journal_folders(ctx.home, storage.inbox_dir, [j["sigla"] for j in journals])
    items, ignored = layout.plan_inbox(ctx.home, storage.inbox_dir)
    listed = {j["sigla"].lower() for j in journals}
    hints = sorted({i.journal_hint for i in items if i.journal_hint})
    unlisted = [h for h in hints if h.lower() not in listed]
    results = [ingest_pdf(ctx.conn, i.path, kind=i.kind, archive_dir=ctx.home / storage.archive_dir,
                          home=ctx.home, journal_hint=i.journal_hint) for i in items]
    summary, files = _summarise(results)
    new = [{"file": layout.shown_path(ctx.home, Path(f["path"])), "pagine": f.get("pages"), "testo": f.get("text_status"),
            "doi_candidato": f.get("doi_candidate"), "origine_doi": f.get("doi_candidate_source"),
            **({"nota_doi": f["doi_candidate_note"]} if f.get("doi_candidate_note") else {})}
           for f in files if f["status"] == "ingested"]
    out = {"summary": summary, "riviste_nelle_cartelle": hints, "errori": [f for f in files if f["status"] == "error"],
           "nuovi": new}
    if created:
        out["cartelle_create"] = created
    if ignored:
        out["ignorati"] = ignored
    if unlisted:
        out["cartelle_non_in_elenco"] = {"sigle": unlisted, "messaggio": f"Aggiungi queste sigle a {storage.journals_csv}."}
    if problems:
        out["problemi_elenco_riviste"] = problems
    _print({**out, **_finish(ctx, wrote=True)})
    return 0 if summary["error"] == 0 else 3


def cmd_doppioni(_args) -> int:
    ctx = _open(write=False)
    report = dedup.build(ctx.conn, ctx.home)
    path = dedup.write_csv(report, ctx.home)
    _print({**dedup.public(report, path), **_finish(ctx, wrote=False)})
    return 0


def cmd_riferimenti(_args) -> int:
    ctx = _open(write=False)
    report = references.build(ctx.conn, ctx.home)
    path = references.write_csv(report, ctx.home)
    _print({**references.public(report, path), **_finish(ctx, wrote=False)})
    return 0


def cmd_write_authors(_args) -> int:
    """Write the authors-v1 proposal (user's yes of 2026-10-11): backup first, one transaction, one audit event."""
    ctx = _open(write=True)
    records, problems = dedup.crossref_records(ctx.conn, ctx.home)
    proposal = dedup.propose_authors(ctx.conn, records)
    out: dict = {"versione": proposal["versione"], "proposta": proposal["riepilogo"]}
    if proposal["collegamenti"]:
        entry = backups.make_backup(ctx.conn, ctx.state, reason="before_write_authors")
        out["backup_prima_della_scrittura"] = str(ctx.state.backup_dir / entry["file"])
        out["scritti"] = dedup.write_authors(ctx.conn, proposal, audit_actor="cli")
    else:
        out["scritti"] = {"autori_creati": 0, "righe_article_author": 0, "articoli": 0}
        out["nota"] = "Nessun articolo nuovo senza autori: niente da scrivere."
    out["nel_database"] = {"author": ctx.conn.execute("SELECT count(*) FROM author").fetchone()[0],
                           "article_author": ctx.conn.execute("SELECT count(*) FROM article_author").fetchone()[0]}
    if problems:
        out["problemi"] = problems[:20]
    _print({**out, **_finish(ctx, wrote=bool(proposal["collegamenti"]))})
    return 0


def cmd_quality_report(_args) -> int:
    ctx = _open(write=False)
    journals, problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    report = quality.build(ctx.conn, ctx.home, journals)
    path = quality.write_csv(report, ctx.home)
    out = {"riepilogo": report["riepilogo"], "file_csv": str(path), "da_controllare": quality.problems(report)}
    if problems:
        out["problemi_elenco_riviste"] = problems
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_ocr(args) -> int:
    _project, home, _db_path, _state = _settings()
    out_dir = home / "rapporti" / "ocr"
    results = []
    for raw in args.paths:
        pdf = Path(raw)
        if not pdf.is_file():
            results.append({"file": raw, "stato": "saltato", "motivo": "file non trovato"})
            continue
        res = ocr.ocr_pdf(pdf, lang=args.lingua)
        row = {k: res.get(k) for k in ("file", "stato", "motivo", "metodo", "pagine_lette")}
        row["pagine"] = len(res["pagine"])
        if res["stato"] == "fatto":
            out_dir.mkdir(parents=True, exist_ok=True)
            txt = out_dir / f"{pdf.stem}.txt"
            txt.write_text(ocr.as_text(res), encoding="utf-8")
            row["file_testo"] = str(txt)
        results.append(row)
    _print({"ocr": results, "versione": ocr.OCR_VERSION, "database": "non aperto"})
    return 0


def cmd_report(_args) -> int:
    ctx = _open(write=False)
    listed, _problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    data = report.build(ctx.conn, ctx.home, journal_list=listed)
    path = report.write_html(data, ctx.home)
    s = data["schede"]
    out = {"file_html": str(path), "articoli": data["articoli"], "pdf": data["documenti"]["pdf"],
           "riviste": [{k: r[k] for k in ("rivista", "da_issn", "articoli", "doi_confermato", "pubmed", "testo_aperto",
                                          "scheda")}
                       for r in data["copertura"]["righe"]],
           "riferimenti": data["citazioni"]["riferimenti"], "riferimenti_verificati": data["citazioni"]["stato"].get("verificato", 0),
           "schede": s["schede_accettate"], "valori": s["valori"],
           "schede_riviste": len(data["confronto"]["righe"]), "metriche_riviste": data["metriche"]["metriche"],
           "avvisi_linee_guida": [policy_changes.summary_line(x) for x in data["avvisi_linee_guida"]["avvisi"]],
           "affermazioni": data["affermazioni"]["affermazioni"]}
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_riviste(_args) -> int:
    ctx = _open(write=False)
    listed, _problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    cat = journals.build(ctx.conn, listed)
    out = {"fonte": cat["fonte"], "riepilogo": cat["riepilogo"],
           "riviste": [{k: j[k] for k in ("titolo", "sigla_elenco", "issn", "editori", "articoli", "cartelle")}
                       | ({"altri_titoli": j["altri_titoli"]} if j["altri_titoli"] else {}) for j in cat["riviste"]]}
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_openalex(args) -> int:
    source = next((s for s in load_sources() if s.id == openalex.SOURCE_ID), None)
    ctx = _open(write=False)
    if args.prova:
        _print({"modo": "prova", "fonte_abilitata": bool(source and source.enabled),
                "da_chiedere": openalex.plan(ctx.conn, args.limit), **_finish(ctx, wrote=False)})
        return 0
    if source is None or source.cost != "free" or not source.enabled:
        raise CliError("La fonte OpenAlex non è abilitata in config/sources.yaml: abilitarla è una decisione "
                       "dell'utente. Usa --prova per vedere cosa verrebbe chiesto (senza rete).")
    secrets = load_secrets()
    if not crossref.valid_email(secrets.contact_email):
        raise CliError("Manca un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL nel file .env di questo PC.")
    limits = ctx.project.limits
    key = secrets.openalex_api_key.get_secret_value() if secrets.openalex_api_key else None
    client = openalex.OpenAlexClient(secrets.contact_email, api_key=key,
                                     max_requests_per_second=limits.max_requests_per_second,
                                     max_retries=limits.max_retries)
    try:
        out = openalex.build(ctx.conn, client, args.limit)
    except crossref.CrossrefError as exc:
        raise CliError(f"OpenAlex: {exc}. Nessun dato scritto.")
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_verify_dois(args) -> int:
    mode = "rivaluta" if args.rivaluta else ("ricontrolla" if args.ricontrolla else "nuovi")
    client = None
    if mode != "rivaluta":
        source = next((s for s in load_sources() if s.id == crossref.SOURCE_ID), None)
        if source is None or source.cost != "free" or not source.enabled:
            raise CliError("La fonte Crossref non è abilitata (o non è gratuita) in config/sources.yaml.")
        if not crossref.valid_email(load_secrets().contact_email):
            raise CliError("Manca un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL nel file .env di questo PC: "
                           "Crossref chiede di indicarlo (polite pool). Aggiungilo e rilancia.")
    ctx = _open(write=True)
    if mode != "rivaluta":
        limits = ctx.project.limits
        client = crossref.CrossrefClient(load_secrets().contact_email, max_requests_per_second=limits.max_requests_per_second,
                                         max_retries=limits.max_retries)
    refreshed = verify_doi.refresh_candidates(ctx.conn) if mode != "rivaluta" else []
    res = verify_doi.run(ctx.conn, home=ctx.home, archive_dir=ctx.home / ctx.project.storage.archive_dir,
                         client=client, mode=mode, limit=args.limit)
    counts = {"confermato": 0, "non trovato": 0, "non corrisponde": 0}
    for p in res.processed:
        counts[p["esito"]] += 1
    total = verify_doi.status_by_document(ctx.conn)
    overall = {"confermato": 0, "non trovato": 0, "non corrisponde": 0}
    for st in total.values():
        overall[st["esito"]] += 1
    n_candidates = len({d["id"] for d in verify_doi.candidates(ctx.conn)}
                       | {d["id"] for d, _ in verify_doi.searched_documents(ctx.conn)})
    out = {
        "modo": mode,
        "riepilogo": {"verificati_ora": len(res.processed), **{k.replace(" ", "_"): v for k, v in counts.items()},
                      "errori": len(res.errors), "gia_verificati": res.skipped, "invariati": res.unchanged,
                      "articoli_creati": sum(1 for p in res.processed if p.get("articolo_creato")),
                      "richieste_a_crossref": res.requests},
        "stato_complessivo": {**{k.replace(" ", "_"): v for k, v in overall.items()},
                              "da_verificare": n_candidates - len(total), "documenti_con_doi": n_candidates,
                              "articoli": ctx.conn.execute("SELECT count(*) FROM article").fetchone()[0]},
        "non_confermati": [p for p in res.processed if p["esito"] != "confermato"],
        "confermati": [{k: p[k] for k in ("file", "doi", "motivo") if k in p} | ({"note": p["note"]} if p.get("note") else {})
                       for p in res.processed if p["esito"] == "confermato"],
        "errori": res.errors,
    }
    if res.stopped:
        out["interrotto"] = res.stopped
    if refreshed:
        out["doi_riletti"] = refreshed
    access = content.align_access(ctx.conn)
    out["accesso"] = {"cambiati_ora": len(access["cambiati"]), **access["per_livello"]}
    _print({**out, **_finish(ctx, wrote=True)})
    return 0 if not res.errors else 3


def cmd_search_dois(args) -> int:
    """cerca-doi: DOIs that the PDF does not print (doi_search.py, rules search-v3), then verified (verify-dois rules, verify-v4)."""
    mode = "ricontrolla" if args.ricontrolla else "nuovi"
    source = next((s for s in load_sources() if s.id == crossref.SOURCE_ID), None)
    if source is None or source.cost != "free" or not source.enabled:
        raise CliError("La fonte Crossref non è abilitata (o non è gratuita) in config/sources.yaml.")
    if not crossref.valid_email(load_secrets().contact_email):
        raise CliError("Manca un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL nel file .env di questo PC: "
                       "Crossref chiede di indicarlo (polite pool). Aggiungilo e rilancia.")
    ctx = _open(write=True)
    journals, problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    limits = ctx.project.limits
    client = crossref.CrossrefClient(load_secrets().contact_email, max_requests_per_second=limits.max_requests_per_second,
                                     max_retries=limits.max_retries)
    res = doi_search.run(ctx.conn, home=ctx.home, archive_dir=ctx.home / ctx.project.storage.archive_dir,
                         client=client, journals=journals, mode=mode, limit=args.limit)
    out = {"modo": mode, "regole": doi_search.RULES_VERSION, "riepilogo": doi_search.summary(res),
           "trovati": [{k: p[k] for k in ("file", "doi", "motivo")} for p in res.processed if p["esito"] == "confermato"],
           "non_confermati": [p for p in res.processed if p["esito"] != "confermato"],
           "non_cercabili": res.not_searchable, "errori": res.errors}
    if problems:
        out["problemi_elenco_riviste"] = problems
    if res.stopped:
        out["interrotto"] = res.stopped
    access = content.align_access(ctx.conn)
    out["accesso"] = {"cambiati_ora": len(access["cambiati"]), **access["per_livello"]}
    _print({**out, **_finish(ctx, wrote=True)})
    return 0 if not res.errors else 3


def cmd_write_journals(args) -> int:
    """scrivi-riviste (Q9, Fase 2): journal / journal_issn from riviste_prioritarie.csv and Crossref; links articles."""
    ctx = _open(write=not args.prova)
    rows, problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    if not rows:
        raise CliError("Elenco delle riviste vuoto o illeggibile: " + "; ".join(problems or ["nessuna riga"]))
    the_plan = journal_table.plan(ctx.conn, ctx.home, rows)
    out = {"modo": "prova" if args.prova else "scrittura", "regole": journal_table.RULES_VERSION,
           "riviste": journal_table.public(the_plan), "articoli_da_collegare": the_plan["articoli_da_collegare"],
           "articoli_senza_rivista_in_elenco": the_plan["articoli_senza_rivista_in_elenco"]}
    if problems:
        out["problemi_elenco_riviste"] = problems
    if not args.prova:
        out["scritto"] = journal_table.write(ctx.conn, the_plan)
    q = lambda sql: ctx.conn.execute(sql).fetchone()[0]
    out["nel_database"] = {"journal": q("SELECT count(*) FROM journal"), "journal_issn": q("SELECT count(*) FROM journal_issn"),
                           "articoli_con_rivista": q("SELECT count(*) FROM article WHERE journal_id IS NOT NULL"),
                           "articoli": q("SELECT count(*) FROM article")}
    _print({**out, **_finish(ctx, wrote=not args.prova)})
    return 0


def _journal_list(ctx: Ctx):
    from .journal_list_write import JournalList, ListLocked
    try:
        return JournalList.load(ctx.home / ctx.project.storage.journals_csv)
    except ListLocked as exc:
        raise CliError(exc.message, code=4)


def _list_guard(fn):
    """Errors of the journal list (file open in Excel, wrong values) as a clear message, nothing written."""
    from .journal_list_write import ListError, ListLocked
    try:
        return fn()
    except ListLocked as exc:
        raise CliError(exc.message, code=4)
    except ListError as exc:
        raise CliError(exc.message, problemi=exc.problems)


def cmd_fix_journals(args) -> int:
    """sistema-riviste (T-054 passo 3): valid siglas, folders merged/renamed, URLs without tracking parameters."""
    from . import journal_folders as jf
    ctx = _open(write=not args.prova)
    storage = ctx.project.storage
    jl = _journal_list(ctx)
    loc = _locator(ctx)
    the_plan = jf.plan_fix(ctx.conn, ctx.home, storage.inbox_dir, jl, loc)
    out = {"modo": "prova" if args.prova else "scrittura", "regole": jf.RULES_VERSION,
           **{k: the_plan[k] for k in ("sigle", "cartelle", "url", "cartelle_da_creare", "cartelle_senza_riga")}}
    wrote = False
    if not args.prova and the_plan["da_fare"]:
        out["fatto"] = _list_guard(lambda: jf.apply_fix(ctx.conn, ctx.home, storage.inbox_dir, jl, loc, the_plan,
                                                         archive_dir=storage.archive_dir))
        wrote = True
    elif not the_plan["da_fare"]:
        out["messaggio"] = "Niente da sistemare."
    loc.save()
    _print({**out, **_finish(ctx, wrote=wrote)})
    return 0 if not (out.get("fatto") or {}).get("problemi") else 3


def cmd_recognise_journals(args) -> int:
    """riconosci-riviste (T-054 passo 4a-b): new rows of the list for journals of confirmed articles not in the list."""
    from . import journal_folders as jf
    client, note = None, None
    if not args.prova and not args.senza_openalex:
        oa = {x.id: x for x in load_sources()}.get(journal_metrics.OPENALEX_SOURCE)
        secrets = load_secrets()
        if oa is not None and oa.enabled and oa.cost == "free" and crossref.valid_email(secrets.contact_email):
            key = secrets.openalex_api_key.get_secret_value() if secrets.openalex_api_key else None
            client = journal_metrics.OpenAlexSources(secrets.contact_email, api_key=key)
        else:
            note = "OpenAlex non usato (fonte non abilitata o e-mail mancante): il sito si aggiunge a mano"
    ctx = _open(write=not args.prova)
    storage = ctx.project.storage
    jl = _journal_list(ctx)
    the_plan = jf.plan_recognise(ctx.conn, ctx.home, jl, inbox_dir=storage.inbox_dir, openalex=client)
    out = {"modo": "prova" if args.prova else "scrittura", "regole": jf.RULES_VERSION,
           "riviste_nuove": [{k: n[k] for k in ("sigla", "titolo", "editore", "issn", "articoli", "doi")}
                             | ({"nota_sito": n["nota_sito"]} if n.get("nota_sito") else {})
                             | {"sito": n["valori"]["sito_ufficiale"]} for n in the_plan["nuove"]],
           "non_create": the_plan["non_create"]}
    if note:
        out["nota"] = note
    wrote = False
    if not args.prova and the_plan["nuove"]:
        out["fatto"] = _list_guard(lambda: jf.apply_recognise(ctx.conn, ctx.home, storage.inbox_dir, jl, the_plan,
                                                               archive_dir=storage.archive_dir))
        out["messaggio"] = ("Riviste nuove aggiunte all'elenco con una sigla proposta: puoi cambiarla nel cruscotto "
                            "(Riviste → Modifica).")
        wrote = True
    elif not the_plan["nuove"]:
        out["messaggio"] = "Nessuna rivista nuova."
    _print({**out, **_finish(ctx, wrote=wrote)})
    return 0


def cmd_move_articles(args) -> int:
    """sposta-articoli (T-054 passo 4c): PDFs of articoli_vari with a journal → riviste/<sigla>/articoli; --annulla."""
    from . import journal_folders as jf
    ctx = _open(write=not args.prova)
    storage = ctx.project.storage
    loc = _locator(ctx)
    if args.annulla:
        reg = jf.latest_registro(ctx.home) if args.annulla == "ultimo" else Path(args.annulla)
        if reg is not None and not reg.is_absolute() and not reg.exists():
            reg = ctx.home / jf.MOVES_DIR / reg
        if reg is None or not reg.is_file():
            raise CliError(f"Registro non trovato: {args.annulla} (i registri sono in {ctx.home / jf.MOVES_DIR})")
        out = {"annulla": str(reg), **jf.undo(ctx.conn, ctx.home, reg, loc, trial=args.prova)}
        loc.save()
        _print({**out, **_finish(ctx, wrote=not args.prova)})
        return 0 if not out["problemi"] else 3
    rows, problems = layout.read_journal_list(ctx.home / storage.journals_csv)
    if args.prova:
        p = jf.plan_moves(ctx.conn, ctx.home, storage.inbox_dir, rows, loc)
        out = {"modo": "prova", "da_spostare": p["da_spostare"], "da_assegnare": p["da_assegnare"],
               "altri": p["altri"], "non_ancora_letti": p["non_ancora_letti"]}
    else:
        r = jf.apply_moves(ctx.conn, ctx.home, storage.inbox_dir, rows, loc)
        out = {"modo": "scrittura", **r,
               "riepilogo": {"spostati": len(r["spostati"]), "da_assegnare": len(r["da_assegnare"]),
                             "problemi": len(r["problemi"])}}
        if r["da_assegnare"]:
            out["messaggio"] = ("Alcuni PDF restano in articoli_vari: scegli la rivista nel cruscotto "
                                "(Articoli → Da assegnare).")
    if problems:
        out["problemi_elenco_riviste"] = problems
    loc.save()
    _print({**out, **_finish(ctx, wrote=not args.prova and bool(out.get("spostati")))})
    return 0 if not out.get("problemi") else 3


def cmd_journal_metrics(args) -> int:
    """metriche-riviste (T-028, Fase 2): journal_metric from the SCImago file in inbox/metriche and from OpenAlex."""
    srcs = {s.id: s for s in load_sources()}
    oa = srcs.get(journal_metrics.OPENALEX_SOURCE)
    use_oa = not args.prova and not args.senza_openalex
    if use_oa and (oa is None or oa.cost != "free" or not oa.enabled):
        raise CliError("La fonte OpenAlex non è abilitata in config/sources.yaml.")
    secrets = load_secrets()
    if use_oa and not crossref.valid_email(secrets.contact_email):
        raise CliError("Manca un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL nel file .env di questo PC.")
    ctx = _open(write=not args.prova)
    js = journal_metrics.journals(ctx.conn)
    if not js:
        raise CliError("Nessuna rivista nella tabella journal: prima fai doppio clic su riviste.cmd.")
    files, problems = [], []
    paths = journal_metrics.scimago_files(ctx.home, ctx.project.storage.inbox_dir)
    for p in paths:
        try:
            files.append(journal_metrics.read_scimago(p))
        except (OSError, journal_metrics.MetricsFileError) as exc:
            problems.append(str(exc))
    folder = ctx.home / ctx.project.storage.inbox_dir / journal_metrics.METRICS_DIR
    if not paths:
        problems.append(f"nessun file di SCImago (scimagojr <anno>.csv) in {folder}: solo OpenAlex")
    sc = journal_metrics.plan_scimago(ctx.conn, files, js)
    client = None
    if use_oa:
        limits = ctx.project.limits
        key = secrets.openalex_api_key.get_secret_value() if secrets.openalex_api_key else None
        client = journal_metrics.OpenAlexSources(secrets.contact_email, api_key=key,
                                                 max_requests_per_second=min(1.0, limits.max_requests_per_second),
                                                 max_retries=limits.max_retries)
    year = int(audit.utc_now()[:4])
    oa_failed = None
    try:
        ox = journal_metrics.plan_openalex(ctx.conn, client, js, year=year) if not args.senza_openalex else []
    except crossref.CrossrefError as exc:
        ox, oa_failed = [], f"OpenAlex: {exc}. Nessuna metrica di OpenAlex scritta (quelle di SCImago sì)."
        problems.append(oa_failed)
    out = {"modo": "prova" if args.prova else "scrittura", "regole": journal_metrics.RULES_VERSION,
           "cartella_scimago": str(folder),
           "file_scimago": [{"file": f.path.name, "anno": f.year, "righe": len(f.rows)} for f in files],
           "scimago": journal_metrics.public(sc), "openalex": journal_metrics.public(ox)}
    if client is not None:
        out["richieste_openalex"] = client.requests_made
    if problems:
        out["problemi"] = problems
    wrote = False
    if not args.prova:
        out["scritto"] = journal_metrics.write(ctx.conn, sc, files, ox, home=ctx.home,
                                               archive_dir=ctx.home / ctx.project.storage.archive_dir)
        wrote = out["scritto"]["riviste_cambiate"] > 0
    out["nel_database"] = journal_metrics.status(ctx.conn)
    out["tabella"] = journal_metrics.table(ctx.conn)
    _print({**out, **_finish(ctx, wrote=wrote)})
    return 3 if oa_failed else 0


def _embedder(ctx, *, need: bool):
    folder = semantic.model_dir(ctx.state.root)
    try:
        return semantic.OnnxEmbedder(folder)
    except semantic.ModelMissing as exc:
        if need:
            raise CliError(f"Ricerca per significato non disponibile: {exc}. Fai doppio clic su semantica.cmd.")
        return None


def cmd_segments(args) -> int:
    """segmenta (T-030): segments of the complete articles (segment-v1)."""
    ctx = _open(write=not args.prova)
    the_plan = segments.plan(ctx.conn)
    out = {"modo": "prova" if args.prova else "scrittura", "regole": segments.RULES_VERSION,
           "riepilogo": segments.summary(the_plan)}
    if not args.prova:
        out["scritto"] = segments.write(ctx.conn, the_plan)
    out["nel_database"] = segments.status(ctx.conn)
    _print({**out, **_finish(ctx, wrote=not args.prova and out["scritto"]["articoli_cambiati"] > 0)})
    return 0


def cmd_vectors(args) -> int:
    """vettori (T-031): download the local model once, then the vectors of the segments without one."""
    ctx = _open(write=not args.prova and not args.solo_scarica)
    folder = semantic.model_dir(ctx.state.root)
    out = {"modello": semantic.MODEL_ID, "cartella_modello": str(folder),
           "modello_presente": (folder / semantic.MODEL_FILE).exists()}
    if args.prova:
        out |= {"modo": "prova", "da_calcolare": len(semantic.missing_segments(ctx.conn)),
                "nel_database": semantic.vector_status(ctx.conn)}
        _print({**out, **_finish(ctx, wrote=False)})
        return 0
    if not out["modello_presente"]:
        try:
            out["scaricato"] = semantic.download_model(folder, log=lambda m: print(m, file=sys.stderr, flush=True))
        except OSError as exc:
            raise CliError(f"Download del modello non riuscito: {exc}. Controlla la connessione e rilancia "
                           "(i file già scaricati restano).")
    if args.solo_scarica:
        _print({**out, "modo": "solo download", **_finish(ctx, wrote=False)})
        return 0
    embedder = _embedder(ctx, need=True)
    out["modo"] = "scrittura"
    out["scritto"] = semantic.write_vectors(ctx.conn, embedder, log=lambda m: print(m, file=sys.stderr, flush=True))
    out["nel_database"] = semantic.vector_status(ctx.conn)
    _print({**out, **_finish(ctx, wrote=out["scritto"]["scritti"] > 0)})
    return 0


def cmd_search_texts(args) -> int:
    """cerca-testi (T-031): search in the segments by words, meaning or both (hybrid-v2); Italian or English."""
    ctx = _open(write=False)
    text = " ".join(args.text)
    mode = args.modo
    embedder = index = None
    if mode in ("ibrida", "significato"):
        embedder = _embedder(ctx, need=mode == "significato")
        if embedder is not None:
            index = semantic.VectorIndex.load(ctx.conn)
        if embedder is None or not index.ids:
            if mode == "significato":
                raise CliError("Nessun vettore nel database: fai doppio clic su semantica.cmd.")
            mode = "parole"
    ranked = semantic.ranked_segments(ctx.conn, text, mode, embedder=embedder, index=index)
    out = {"domanda": text, "modo": mode, "regole": semantic.RULES_VERSION,
           "risultati": semantic.by_article(ctx.conn, ranked, limit=args.limit)}
    if mode != args.modo:
        out["nota"] = "vettori o modello non disponibili su questo PC: ricerca solo per parole (semantica.cmd)"
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_search_benchmark(args) -> int:
    """benchmark-ricerca (T-032): recall@k of the search methods on the questions checked by the user."""
    ctx = _open(write=False)
    path = Path(args.file) if args.file else search_benchmark.latest_filled(ctx.home)
    if path is None or not path.exists():
        raise CliError("Nessun file di domande compilato (rapporti\\benchmark_ricerca_<n>_<data>_compilato.csv).")
    qs, info = search_benchmark.questions(path, ctx.conn)
    embedder = _embedder(ctx, need=False)
    index = semantic.VectorIndex.load(ctx.conn) if embedder is not None else None
    res = search_benchmark.run(ctx.conn, qs, embedder=embedder, index=index)
    out = {"file": str(path), **info, **{k: v for k, v in res.items() if k != "per_domanda"},
           "file_risultati": str(search_benchmark.write_results(res, ctx.home))}
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_claims(_args) -> int:
    """affermazioni (T-033): submit the claims files waiting in rapporti/affermazioni/da_inviare."""
    ctx = _open(write=True)
    folder = claims.to_send_dir(ctx.home)
    folder.mkdir(parents=True, exist_ok=True)
    results = claims.submit_folder(ctx.conn, ctx.home)
    out = {"regole": claims.CLAIM_VERSION, "livello": claims.APPRAISAL_RULES, "cartella": str(folder),
           "file_trovati": len(results), "accettati": sum(1 for r in results if r.get("esito") == "accettato"),
           "rifiutati": sum(1 for r in results if r.get("esito") == "rifiutato"),
           "non_inviati": sum(1 for r in results if r.get("esito") == "non inviato"), "file": results,
           "stato": claims.status(ctx.conn)}
    _print({**out, **_finish(ctx, wrote=bool(results))})
    return 0 if not out["rifiutati"] else 3


def cmd_claims_control(_args) -> int:
    """affermazioni-controllo (T-033): the precision of a filled copy with new S/N, else the CSV of the claims never
    checked, else the latest results (claims.control_step)."""
    ctx = _open(write=True)
    res = claims.control_step(ctx.conn, ctx.home)
    _print({**res, **_finish(ctx, wrote=res.get("affermazioni_registrate", 0) > 0)})
    return 0


def cmd_check_claims(args) -> int:
    """controlla-affermazioni FILE: every check of `affermazioni` on one file; nothing is written."""
    try:
        data = json.loads(Path(args.file).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"File non leggibile: {exc}") from exc
    ctx = _open(write=False)
    outcome, _doc = claims.validate(ctx.conn, data)
    out = {"esito": "accettabile" if outcome.accepted else "da correggere",
           "controlli": {c.name: ("ok" if c.ok else "non superato") for c in outcome.checks},
           "problemi": outcome.problems()[:40]}
    if outcome.card is not None:
        level, reasons = claims.appraisal(ctx.conn, outcome.card.article_id)
        out["livello"], out["perche"] = level, reasons
    _print({**out, **_finish(ctx, wrote=False)})
    return 0 if outcome.accepted else 3


def _engine_env(ctx) -> "engine.Env":
    return engine.Env(ctx.conn, ctx.home, ctx.project, ctx.state.root)


def cmd_lavora(args) -> int:
    """lavora (T-035, Fase 4): the steps in order, only what is missing (engine-v1)."""
    if args.piano or args.fine:
        ctx = _open(write=False)
        env = _engine_env(ctx)
        if args.piano:
            out = {"modo": "piano", **engine.plan(env, counts=args.conti, group=args.gruppo)}
            if args.conti:
                out |= engine.work_for_others(env)
        else:
            if not args.giro:
                raise CliError("Manca --giro: il nome del giro da chiudere.")
            released = engine.Lock(ctx.state.root, args.giro, 0).release()
            out = {"modo": "fine", "giro": args.giro, "blocco_tolto": released,
                   "passi": engine.giro_summary(ctx.conn, args.giro), **engine.work_for_others(env)}
        _print({**out, **_finish(ctx, wrote=False)})
        return 0
    ctx = _open(write=True)
    env = _engine_env(ctx)
    auto = args.giro is None
    giro = args.giro or "manuale_" + datetime.now().strftime("%Y-%m-%d_%H%M%S")
    lock = engine.Lock(ctx.state.root, giro, args.pid or os.getpid())
    try:
        taken_over = lock.acquire()
    except engine.Busy as exc:
        raise CliError(f"lavora è già in corso su questo PC (giro {exc.info.get('giro')}, dal {exc.info.get('iniziato')}): "
                       "aspetta che finisca.", code=6, in_corso=exc.info)
    try:
        closed = engine.close_interrupted(ctx.conn)
        if args.passo:
            out = {"modo": "passo", "giro": giro, **engine.run_step(env, engine.BY_KEY[args.passo], giro=giro, lock=lock)}
        else:
            out = {"modo": "tutti", **engine.run_all(env, giro=giro, lock=lock), **engine.work_for_others(env)}
        if closed:
            out["interrotti_ripresi"] = closed
        if taken_over:
            out["blocco_vecchio"] = taken_over
    finally:
        if auto:
            lock.release()
    steps = out["passi"] if "passi" in out else [out]
    stopped = any(s.get("esito") == "fermo" for s in steps)
    wrote = bool(closed) or any(s.get("esito") not in ("niente da fare", None) and "workflow_run_id" in s for s in steps)
    _print({**out, **_finish(ctx, wrote=wrote)})
    return 4 if stopped else 0


def cmd_cruscotto(args) -> int:
    """cruscotto (T-036, Fase 5): the dashboard in the browser, served by this PC on 127.0.0.1 (dashboard-v1)."""
    ctx = _open(write=False)                       # same checks as every command (database, migrations, copy)
    _finish(ctx, wrote=False)
    _close_all()
    from . import dashboard
    try:
        import starlette  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:
        raise CliError(f"Mancano i pacchetti del cruscotto ({exc.name}): fai doppio clic su installa_orpheus.cmd.") from exc
    settings = dashboard.Settings.from_config(Path(args.comandi) if args.comandi else None)
    try:
        dashboard.serve(settings, port=args.porta, open_browser=not args.senza_browser)
    except OSError as exc:
        raise CliError(f"Cruscotto non avviato: {exc}") from exc
    return 0


def cmd_manoscritto(args) -> int:
    """manoscritto (T-037, Fase 6): a Word .docx manuscript checked against the journal cards (compliance-v1).
    Read-only on the database. R10: the text of the manuscript never goes to the output (which ends in the log on
    GitHub): numbers and outcomes only; the full report stays on the PC (rapporti/manoscritti)."""
    from . import compliance, manuscript
    ctx = _open(write=False)
    listed, _problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    if args.riviste:
        rows = compliance.journals_with_cards(ctx.conn, listed)
        _print({"riviste": [{"sigla": j["sigla"], "titolo": j["titolo"], "data_scheda": j["data_scheda"][:10]}
                            for j in rows], "cartella_manoscritti": str(manuscript.folder(ctx.home)),
                **_finish(ctx, wrote=False)})
        return 0
    name = args.file or os.environ.get("ORPHEUS_MANOSCRITTO")
    if not name:
        raise CliError("Indica il file del manoscritto (.docx), per esempio quello nella cartella "
                       f"{manuscript.folder(ctx.home)}")
    path = Path(name)
    if not path.exists() and not path.is_absolute():
        path = manuscript.folder(ctx.home) / name
    if not path.is_file():
        raise CliError("File del manoscritto non trovato" + ("" if os.environ.get("ORPHEUS_MANOSCRITTO") else f": {name}"))
    if not compliance.journals_with_cards(ctx.conn, listed):
        raise CliError("Nessuna rivista con la scheda nel database: prima le schede delle riviste (schede_riviste.cmd).")
    ref_client = None
    if args.verifica_riferimenti:            # T-038: network, only DOIs and cited titles leave the PC
        source = next((s for s in load_sources() if s.id == crossref.SOURCE_ID), None)
        if source is None or source.cost != "free" or not source.enabled:
            raise CliError("La fonte Crossref non è abilitata (o non è gratuita) in config/sources.yaml.")
        if not crossref.valid_email(load_secrets().contact_email):
            raise CliError("Manca un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL nel file .env di questo PC: "
                           "Crossref chiede di indicarlo (polite pool). Aggiungilo e rilancia.")
        ref_client = crossref.CrossrefClient(load_secrets().contact_email,
                                             max_requests_per_second=ctx.project.limits.max_requests_per_second,
                                             max_retries=ctx.project.limits.max_retries)
    try:
        from . import journal_match              # T-040 parte 2: closeness by meaning when the model is on this PC
        result = compliance.run(ctx.conn, path, listed, args.rivista, args.tipo, verify_refs=args.verifica_riferimenti,
                                ref_client=ref_client, local_dir=ctx.state.root,
                                matcher=journal_match.Matcher.from_local(ctx.conn, ctx.state.root))
    except manuscript.ManuscriptError as exc:
        raise CliError(f"Manoscritto non leggibile: {exc}", code=3) from exc
    if not result["riviste"]:
        raise CliError("Nessuna delle riviste indicate ha una scheda: " + ", ".join(result["riviste_sconosciute"]))
    report_path = None if args.senza_rapporto else compliance.write_report(result, ctx.home, path.name)
    _print({**compliance.summary(result, report_path), **_finish(ctx, wrote=False)})
    return 0


def cmd_scheda_unica(args) -> int:
    """scheda-unica (T-051c, cards-v2): convert the cards-v1 cards, correct one value, export the cards."""
    from .eurydike import store
    ctx = _open(write=bool(args.converti or args.correggi))
    out: dict = {"versione": store.CARD_VERSION}
    wrote = False
    try:
        if args.converti:
            entry = backups.make_backup(ctx.conn, ctx.state, reason="before_cards_v2")
            out["backup_prima_della_scrittura"] = str(ctx.state.backup_dir / entry["file"])
            out["conversione"] = store.convert_v1(ctx.conn, actor="cli")
            wrote = bool(out["conversione"]["convertite"])
        if args.correggi:
            aid, field = args.correggi
            state = "confirmed" if args.conferma else "nr" if args.non_riportato else "edited"
            out["correzione"] = store.set_item(ctx.conn, aid, field, state=state, item_ord=args.ordine,
                                               value=args.valore, quote=args.citazione, page=args.pagina,
                                               note=args.nota, actor="cli")
            out["esportata"] = store.export_card(ctx.conn, aid, ctx.home)
            wrote = True
        if args.esporta is not None or args.tutte:
            ids = store.articles_with_card(ctx.conn) if args.tutte else [args.esporta]
            files = [store.export_card(ctx.conn, a, ctx.home) for a in ids]
            out["esportate"] = len(files)
            out["cartella"] = str(store.export_dir(ctx.home))
            out["corretti_a_mano"] = sum(f["corretti_a_mano"] for f in files)
            out["da_controllare"] = sum(f["da_controllare"] for f in files)
    except store.StoreError as e:
        out["errore"] = str(e)
        _print({**out, **_finish(ctx, wrote=wrote)})
        return 2
    _print({**out, **_finish(ctx, wrote=wrote)})
    return 0


def cmd_revisione_sistematica(args) -> int:
    """revisione-sistematica (T-051b, EURYDIKE E1, review-audit-v1): a systematic review / meta-analysis in Word checked
    for internal consistency (PRISMA flow, abstract = flow, table of studies, k/n, CIs, I², registration, risk of bias,
    conversions) plus the PRISMA 2020 checklist and the numbers. No journal and no database needed; read-only.
    R10: the output has numbers and outcomes only; the report stays on the PC (rapporti/manoscritti)."""
    from . import manuscript
    from .eurydike import review_audit
    _project, home, _db, _state = _settings()
    if args.cartella:
        _print({"cartella_manoscritti": str(manuscript.folder(home))})
        return 0
    name = args.file or os.environ.get("ORPHEUS_MANOSCRITTO")
    if not name:
        raise CliError(f"Indica il file della revisione (.docx), per esempio nella cartella {manuscript.folder(home)}")
    path = Path(name)
    if not path.exists() and not path.is_absolute():
        path = manuscript.folder(home) / name
    if not path.is_file():
        raise CliError("File della revisione non trovato" + ("" if os.environ.get("ORPHEUS_MANOSCRITTO") else f": {name}"))
    try:
        result = review_audit.run(path)
    except manuscript.ManuscriptError as exc:
        raise CliError(f"Revisione non leggibile: {exc}", code=3) from exc
    report = None if args.senza_rapporto else review_audit.write_report(result, home, path.name)
    _print(review_audit.summary(result, report))
    return 0


def _one_journal_manuscript(args):
    """(ctx, manuscript, compliance result for ONE journal) for revisione, lettera and risposta. R10: the path may
    come from ORPHEUS_MANOSCRITTO so that it is not in the log."""
    from . import compliance, manuscript
    ctx = _open(write=False)
    listed, _problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    name = args.file or os.environ.get("ORPHEUS_MANOSCRITTO")
    if not name:
        raise CliError("Indica il file del manoscritto (.docx) della cartella manoscritti")
    path = Path(name)
    if not path.exists() and not path.is_absolute():
        path = manuscript.folder(ctx.home) / name
    if not path.is_file():
        raise CliError("File del manoscritto non trovato" + ("" if os.environ.get("ORPHEUS_MANOSCRITTO") else f": {name}"))
    if not args.rivista:
        raise CliError("Indica una rivista con --rivista SIGLA (una sola rivista)")
    try:
        m = manuscript.read(path)
        result = compliance.run(ctx.conn, path, listed, [args.rivista], args.tipo, local_dir=ctx.state.root)
    except manuscript.ManuscriptError as exc:
        raise CliError(f"Manoscritto non leggibile: {exc}", code=3) from exc
    if not result["riviste"]:
        raise CliError(f"La rivista {args.rivista} non ha una scheda: prima schede_riviste.cmd.")
    return ctx, m, result, listed


def _check_only(args, checker, verdict) -> int:
    packet = json.loads(Path(args.pacchetto).read_text(encoding="utf-8")) if args.pacchetto else None
    if packet is None:
        raise CliError("Indica il pacchetto con --pacchetto <pacchetto.json>")
    try:
        data = json.loads(Path(args.controlla).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"File non leggibile: {exc}") from exc
    res = checker(packet, data)
    _print({"esito": verdict(res), "controlli": [{"nome": r.name, "ok": r.ok, "problemi": r.problems} for r in res]})
    return 0 if verdict(res) == "accettabile" else 3


def cmd_revisione(args) -> int:
    """revisione (T-040 parte 3, review-v1): simulated peer review of a manuscript for ONE journal. Prepares the packet
    for a session with Claude and checks the review Claude wrote. Read-only on the database. R10: the output has
    counts and outcomes only (no text of the manuscript); packet and review stay in rapporti/manoscritti/revisione."""
    from . import peer_review as pr
    if args.controlla:                                  # for Claude, in a session with the PC
        return _check_only(args, pr.check_review, pr.verdict)
    ctx, m, result, _listed = _one_journal_manuscript(args)
    packet = pr.build_packet(m, result)
    pr.write_packet(ctx.home, packet)
    out = pr.process(ctx.home, packet)
    _print({"versione": pr.REVIEW_VERSION, "impronta": packet["impronta"], "rivista": packet["rivista"]["sigla"],
            "tipo": packet["tipo"], "paragrafi": len(packet["manoscritto"]["paragrafi"]),
            "controlli_da_correggere": sum(c["esito"] == "ko" for c in packet["controlli"]),
            "controlli_da_guardare": sum(c["esito"] == "guarda" for c in packet["controlli"]), **out,
            "nota": "Revisione simulata, non una previsione dell'esito.", **_finish(ctx, wrote=False)})
    return 0


def cmd_validazione(args) -> int:
    """validazione (Fase 7, T-046, validation-v1): control file for the hand check of what ORPHEUS said about ONE
    manuscript for ONE journal (references, compliance, numbers, simulated review) and, once filled, precision with
    the Wilson 95% interval against the thresholds of the plan. Read-only on the database. R10: the output has counts
    and outcomes only; the control file (which quotes the manuscript) stays in rapporti/manoscritti/validazione."""
    from . import health, peer_review as pr, submission_letters as sl, validation as val
    ctx, _m, result, _listed = _one_journal_manuscript(args)
    fp, sigla = result["manoscritto"]["impronta"], result["riviste"][0]["rivista"]
    prep = val.prepare(ctx.home, result)
    d = val.folder(ctx.home, fp, sigla)
    h = health.check(ctx.conn, state=ctx.state, project=ctx.project, db_path=ctx.db_path, home=ctx.home)
    res = val.score(d, letter_dir=sl.folder(ctx.home, sl.COVER, fp, sigla),
                    response_dir=sl.folder(ctx.home, sl.RESPONSE, fp, sigla), health=h)
    val.write_results(d, res)
    _print({"versione": val.VALIDATION_VERSION, "impronta": fp, "rivista": sigla, **prep,
            "revisione_accettata": (pr.folder(ctx.home, fp, sigla) / pr.ACCEPTED).exists(),
            "compilato": res.get("file_compilato") is not None,
            "criteri": {k: {x: v[x] for x in ("esito", "righe", "si", "no", "precisione", "ic95", "non_compilate",
                                                 "inventati", "mancati", "falsi_verificati") if x in v}
                        for k, v in res["criteri"].items()},
            "riepilogo": res["riepilogo"], "gate": res["gate"], **_finish(ctx, wrote=False)})
    return 0


def _letter_layout() -> dict:
    from . import article_docx
    return {**article_docx.DEFAULT_LAYOUT, "interlinea": "singola", "numeri_di_riga": False}


def cmd_lettera(args) -> int:
    """lettera (Fase 6, cover-v1): cover letter to the editor of ONE journal, written by Claude in a session and checked
    here; accepted -> lettera.docx. Read-only on the database; output: counts and outcomes only (R10)."""
    from . import compliance, submission_letters as sl
    if args.controlla:
        return _check_only(args, sl.check_cover, sl.verdict)
    ctx, m, result, listed = _one_journal_manuscript(args)
    journal = compliance.pick_journals(ctx.conn, listed, [args.rivista])[0][0]
    rules = compliance.load_rules(ctx.conn, journal, result["tipo"])
    packet = sl.cover_packet(m, result, rules)
    sl.write_cover(ctx.home, packet)
    out = sl.process(ctx.home, sl.COVER, packet, _letter_layout())
    _print({"versione": sl.COVER_VERSION, "impronta": packet["impronta"], "rivista": packet["rivista"]["sigla"],
            "tipo": packet["tipo_testo"], "regole_lettera": len(packet["regole_lettera"]), **out,
            **_finish(ctx, wrote=False)})
    return 0


def cmd_risposta(args) -> int:
    """risposta (Fase 6, response-v1): response to the reviewers from the comments pasted in commenti_revisori.txt and
    the REVISED manuscript; written by Claude in a session, checked here; accepted -> risposta_revisori.docx."""
    from . import submission_letters as sl
    if args.controlla:
        return _check_only(args, sl.check_response, sl.verdict)
    ctx, m, result, _listed = _one_journal_manuscript(args)
    fp, sigla = result["manoscritto"]["impronta"], result["riviste"][0]["rivista"]
    cfile = sl.comments_file(ctx.home, fp, sigla)
    comments = sl.parse_comments(cfile.read_text(encoding="utf-8-sig"))
    base = {"versione": sl.RESPONSE_VERSION, "impronta": fp, "rivista": sigla, "commenti": len(comments),
            "revisori": sorted({c["revisore"] for c in comments})}
    if not comments:
        _print({**base, "esito": "servono i commenti", "cartella": str(cfile.parent), **_finish(ctx, wrote=False)})
        return 0
    packet = sl.response_packet(m, result, comments)
    sl.write_response(ctx.home, packet)
    out = sl.process(ctx.home, sl.RESPONSE, packet, _letter_layout())
    _print({**base, **out, **_finish(ctx, wrote=False)})
    return 0


def cmd_nuovo_articolo(args) -> int:
    """nuovo-articolo (T-041, Fase 6 A): a new article from the user's data, section by section (writing-v1).
    Nothing is written in the database. R10: the output has states and counts only (no name, no text); the project
    name may come from ORPHEUS_PROGETTO so that it is not in the log."""
    from . import new_article as na
    if args.controlla:                                  # for Claude, in a session: one section file, nothing moved
        packet_path = Path(args.pacchetto) if args.pacchetto else None
        if packet_path is None or not packet_path.exists():
            raise CliError("Indica il pacchetto con --pacchetto <pacchetto.json>")
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        try:
            data = json.loads(Path(args.controlla).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CliError(f"Sezione non leggibile: {exc}") from exc
        checks = na.check_section(packet, data)
        _print({"esito": na.verdict(checks), "controlli": [{"nome": c.name, "ok": c.ok, "problemi": c.problems}
                                                           for c in checks]})
        return 0
    ctx = _open(write=False)
    name = args.nome or os.environ.get("ORPHEUS_PROGETTO")
    if args.elenco or not name:
        _print({"progetti": len(na.list_projects(ctx.home)), "cartella": str(na.folder(ctx.home)),
                **_finish(ctx, wrote=False)})
        return 0
    listed, _problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    try:
        d = na.project_dir(ctx.home, name)
        if not d.exists():
            if args.rivista and not compliance_journal_known(ctx.conn, listed, args.rivista):
                raise CliError(f"Rivista sconosciuta o senza scheda: {args.rivista}")
            d = na.create(ctx.home, name, args.rivista, args.tipo)
            files = ["studio.csv"] + [f for f in ("risultati.csv", na.INCLUDED_FILE) if (d / f).exists()]
            _print({"creato": True, "tipo": args.tipo, "cartella": str(na.folder(ctx.home)), "da_compilare": files,
                    **_finish(ctx, wrote=False)})
            return 0
        if args.approva:
            na.approve(d, args.approva)
            _print({"approvata": args.approva, **na.public_state(d, None), **_finish(ctx, wrote=False)})
            return 0
        from . import journal_match
        packet, problems = na.current_packet(ctx.conn, ctx.home, name, listed,
                                             lambda: journal_match.Matcher.from_local(ctx.conn, ctx.state.root))
    except na.WritingError as exc:
        raise CliError(str(exc)) from exc
    if problems:
        _print({"pronto": False, "problemi": problems, "cartella": str(na.folder(ctx.home)), **_finish(ctx, wrote=False)})
        return 3
    if args.bozza:                                      # T-041 part 2: the Word draft from the approved sections
        from . import article_docx as ad
        try:
            out = ad.build(d, packet, ctx.conn, journal_list=listed)
        except ad.DraftError as exc:
            raise CliError(str(exc)) from exc
        _print({"bozza": out, "cartella": str(na.folder(ctx.home)), **_finish(ctx, wrote=False)})
        return 0 if out["gate"]["superato"] else 4
    sent = na.submit(d, packet) if any((d / "sezioni" / "da_inviare").glob("*.json")) else []
    if sent:
        packet["sezioni"] = na.state(d)["sezioni"]
        (d / "pacchetto.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        na.write_preview(d, packet)
    _print({"pronto": True, **na.public_state(d, packet), "inviate": sent, "cartella": str(na.folder(ctx.home)),
            "anteprima": "anteprima.html", **_finish(ctx, wrote=False)})
    return 0


def compliance_journal_known(conn, listed, sigla) -> bool:
    from . import compliance
    found, unknown = compliance.pick_journals(conn, listed, [sigla])
    return bool(found) and not unknown


def cmd_policy_versions(args) -> int:
    """linee-guida (Fase 2): dated versions of the author guidelines PDFs (journal_policy_version, policy-v2)."""
    ctx = _open(write=not args.prova)
    rows, problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
    if not rows:
        raise CliError("Elenco delle riviste vuoto o illeggibile: " + "; ".join(problems or ["nessuna riga"]))
    the_plan = policy_versions.plan(ctx.conn, rows, home=ctx.home, locator=_locator(ctx))
    out = {"modo": "prova" if args.prova else "scrittura", "regole": policy_versions.RULES_VERSION,
           "linee_guida": policy_versions.public(the_plan), "versioni": policy_versions.public_versions(the_plan),
           "da_scrivere": the_plan["da_scrivere"]}
    if problems:
        out["problemi_elenco_riviste"] = problems
    if not args.prova:
        out["scritto"] = policy_versions.write(ctx.conn, the_plan)
    q = lambda sql: ctx.conn.execute(sql).fetchone()[0]
    out["nel_database"] = {
        "versioni": q("SELECT count(*) FROM journal_policy_version WHERE policy_type = 'author_guidelines'"),
        "riviste_con_linee_guida": q("SELECT count(DISTINCT journal_id) FROM journal_policy_version "
                                     "WHERE policy_type = 'author_guidelines'"),
        "da_rileggere": q("SELECT count(*) FROM journal_policy_version WHERE needs_human_review = 1"),
        "linee_guida_lette": q("SELECT count(*) FROM document WHERE kind = 'author_guidelines'")}
    sigla_of = {jid: sg.upper() for sg, jid in policy_versions.journal_by_sigla(ctx.conn, rows).items()}
    out["avvisi"] = policy_changes.alerts(ctx.conn, sigla_of)
    out["regole_avvisi"] = policy_changes.RULES_VERSION
    _print({**out, **_finish(ctx, wrote=not args.prova)})
    return 0


def cmd_verify_pubmed(args) -> int:
    mode = "ricontrolla" if args.ricontrolla else "nuovi"
    if not args.prova:
        source = next((s for s in load_sources() if s.id == pubmed.SOURCE_ID), None)
        if source is None or source.cost != "free" or not source.enabled:
            raise CliError("La fonte PubMed non è abilitata (o non è gratuita) in config/sources.yaml.")
        if not pubmed.valid_email(load_secrets().contact_email):
            raise CliError("Manca un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL nel file .env di questo PC: "
                           "NCBI chiede di indicarlo (parametro email). Aggiungilo e rilancia.")
    ctx = _open(write=not args.prova)
    if args.prova:
        todo, skipped = verify_pubmed.select(ctx.conn, mode)
        n = len(todo) if args.limit is None else min(len(todo), args.limit)
        _print({"modo": "prova", "da_chiedere": n, "gia_verificati": skipped, **verify_pubmed.estimate(n),
                "stato_complessivo": verify_pubmed.overall(ctx.conn), **_finish(ctx, wrote=False)})
        return 0
    limits = ctx.project.limits
    client = pubmed.PubMedClient(load_secrets().contact_email, max_requests_per_second=min(1.0, limits.max_requests_per_second),
                                 max_retries=limits.max_retries)
    res = verify_pubmed.run(ctx.conn, home=ctx.home, archive_dir=ctx.home / ctx.project.storage.archive_dir,
                            client=client, mode=mode, limit=args.limit)
    counts = {v: 0 for v in verify_pubmed.OUTCOME_IT.values()}
    for p in res.processed:
        counts[p["esito"]] += 1
    out = {
        "modo": mode,
        "riepilogo": {"verificati_ora": len(res.processed), **{k.replace(" ", "_"): v for k, v in counts.items()},
                      "errori": len(res.errors), "gia_verificati": res.skipped, "richieste_a_pubmed": res.requests},
        "stato_complessivo": verify_pubmed.overall(ctx.conn),
        "da_guardare": [p for p in res.processed if p["esito"] != "trovato"],
        "avvisi_pubmed": [p for p in res.processed if p.get("avvisi")],
        "errori": res.errors,
        "nota_ncbi": ("Dati da NCBI E-utilities (PubMed). Disclaimer e copyright di NCBI: " + pubmed.DISCLAIMER_URL
                      + " - gli abstract possono avere copyright: ORPHEUS salva solo PMID, MeSH e tipi di pubblicazione."),
    }
    if res.stopped:
        out["interrotto"] = res.stopped
    _print({**out, **_finish(ctx, wrote=bool(res.processed) or bool(res.errors))})
    return 0 if not res.errors else 3


def cmd_europepmc(args) -> int:
    """Read-only report on the open-access full texts in Europe PMC (T-010 parte 2b): nothing is written."""
    if not args.prova:
        source = next((s for s in load_sources() if s.id == europepmc.SOURCE_ID), None)
        if source is None or source.cost != "free" or not source.enabled:
            raise CliError("La fonte Europe PMC non è abilitata (o non è gratuita) in config/sources.yaml.")
        if not europepmc.valid_email(load_secrets().contact_email):
            raise CliError("Manca un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL nel file .env di questo PC: "
                           "ORPHEUS lo mette nello User-Agent come contatto. Aggiungilo e rilancia.")
    save = args.salva and not args.prova
    ctx = _open(write=save)
    mode = "ricontrolla" if args.ricontrolla else "nuovi"
    if args.salva:
        todo, skipped = europepmc_survey.select(ctx.conn, mode)
    else:
        todo, skipped = europepmc_survey.candidates(ctx.conn), 0
    n = len(todo) if args.limit is None else min(len(todo), max(0, args.limit))
    if args.prova:
        out = {"modo": "prova", "da_cercare": n, "gia_cercati": skipped, **europepmc_survey.estimate(n),
               "scritto_nel_database": False}
        if args.salva:
            out["stato_complessivo"] = europepmc_survey.overall(ctx.conn)
        _print({**out, **_finish(ctx, wrote=False)})
        return 0
    out: dict = {"modo": "salva" if save else "rapporto"}
    if save and n:
        entry = backups.make_backup(ctx.conn, ctx.state, reason="before_europepmc")
        out["backup_prima_della_scrittura"] = str(ctx.state.backup_dir / entry["file"])
    rps = min(1.0, ctx.project.limits.max_requests_per_second)
    client = europepmc.EuropePMCClient(load_secrets().contact_email, max_requests_per_second=rps,
                                       max_retries=ctx.project.limits.max_retries)
    res = europepmc_survey.run(ctx.conn, client, limit=args.limit, save=save, home=ctx.home,
                               archive_dir=ctx.home / ctx.project.storage.archive_dir, mode=mode)
    path = europepmc_survey.write_csv(res, ctx.home)
    out |= europepmc_survey.public(res, path)
    if save:
        out["stato_complessivo"] = europepmc_survey.overall(ctx.conn)
    _print({**out, **_finish(ctx, wrote=bool(res.written))})
    return 0 if not res.errors else 3


def cmd_verify_citations(args) -> int:
    """T-014 parte 2 (user's yes of 2026-10-11): rules cite-v1, backup first, one transaction per article."""
    if args.rivaluta:
        ctx = _open(write=True)
        out: dict = {"modo": "rivaluta"}
        if ctx.conn.execute("SELECT 1 FROM citation WHERE ref_source = ? LIMIT 1", (verify_citations.REF_SOURCE,)).fetchone():
            entry = backups.make_backup(ctx.conn, ctx.state, reason="before_reevaluate_citations")
            out["backup_prima_della_scrittura"] = str(ctx.state.backup_dir / entry["file"])
        res = verify_citations.rivaluta(ctx.conn, home=ctx.home)
        out |= {**res, "stato_complessivo": verify_citations.overall(ctx.conn),
                "discordanti_esempi": verify_citations.mismatches(ctx.conn, 30)}
        _print({**out, **_finish(ctx, wrote=bool(res["righe_aggiornate"]))})
        return 0
    if not args.prova:
        source = next((s for s in load_sources() if s.id == crossref.SOURCE_ID), None)
        if source is None or source.cost != "free" or not source.enabled:
            raise CliError("La fonte Crossref non è abilitata (o non è gratuita) in config/sources.yaml.")
        if not crossref.valid_email(load_secrets().contact_email):
            raise CliError("Manca un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL nel file .env di questo PC: "
                           "Crossref chiede di indicarlo (polite pool). Aggiungilo e rilancia.")
    ctx = _open(write=not args.prova)
    p = verify_citations.plan(ctx.conn, ctx.home, args.limit)
    if args.prova:
        _print({"modo": "prova", **p, "stato_complessivo": verify_citations.overall(ctx.conn),
                "scritto_nel_database": False, **_finish(ctx, wrote=False)})
        return 0
    out: dict = {"modo": "verifica"}
    if p["articoli_da_verificare"]:
        entry = backups.make_backup(ctx.conn, ctx.state, reason="before_verify_citations")
        out["backup_prima_della_scrittura"] = str(ctx.state.backup_dir / entry["file"])
    limits = ctx.project.limits
    client = crossref.CrossrefClient(load_secrets().contact_email, max_requests_per_second=limits.max_requests_per_second,
                                     max_retries=limits.max_retries)
    res = verify_citations.run(ctx.conn, home=ctx.home, archive_dir=ctx.home / ctx.project.storage.archive_dir,
                               client=client, limit=args.limit)
    out |= {
        "regole": verify_citations.RULES_VERSION,
        "riepilogo": {"articoli_verificati_ora": len(res.articles), "riferimenti_scritti": sum(res.counts.values()),
                      **{verify_citations.STATUS_IT[k].replace(" ", "_"): v for k, v in res.counts.items()},
                      "richieste_a_crossref": res.requests, "gruppi": res.batches, "doi_chiesti_da_soli": res.singles,
                      "errori": len(res.errors), "articoli_rimandati": res.skipped_articles},
        "stato_complessivo": verify_citations.overall(ctx.conn),
        "discordanti_esempi": verify_citations.mismatches(ctx.conn, 20),
        "errori": res.errors[:20],
    }
    if res.problems:
        out["problemi"] = res.problems[:20]
    if res.stopped:
        out["interrotto"] = res.stopped
    _print({**out, **_finish(ctx, wrote=bool(res.articles))})
    return 0 if not res.errors else 3


def cmd_align_access(_args) -> int:
    ctx = _open(write=True)
    res = content.align_access(ctx.conn)
    _print({"cambiati_ora": len(res["cambiati"]), "per_livello": res["per_livello"], "elenco": res["cambiati"],
            **_finish(ctx, wrote=bool(res["cambiati"]))})
    return 0


def cmd_separate(_args) -> int:
    ctx = _open(write=False)
    res = content.separate(ctx.conn, ctx.home, ctx.project.storage.inbox_dir, locator=_locator(ctx))
    classes = content.classify_all(ctx.conn)
    counts = {k.replace(" ", "_"): sum(1 for c in classes.values() if c["classe"] == k) for k in content.CLASSES}
    out = {"spostati": len(res["spostati"]), "cartella": str(ctx.home / content.SEPARATED_DIR),
           "contenuto_nel_database": counts, "elenco": res["spostati"], "problemi": res["problemi"]}
    _print({**out, **_finish(ctx, wrote=False)})
    return 0 if not res["problemi"] else 3


def cmd_benchmark_csv(args) -> int:
    ctx = _open(write=False)
    sample = benchmark.build(ctx.conn, size=args.righe, locator=_locator(ctx))
    path, written = benchmark.write(sample, ctx.home)
    out = {"file_csv": str(path), "nuovo_file": written, "righe": len(sample.rows),
           "campione": {k.replace(" ", "_"): v for k, v in sample.chosen.items()},
           "disponibili": {k.replace(" ", "_"): v for k, v in sample.population.items()},
           "mai_verificati_esclusi": sample.not_verified, "non_completi_esclusi": sample.not_complete,
           "seme": benchmark.SEED,
           "file_da_salvare": str(path.with_name(f"{path.stem}_compilato.csv"))}
    if not written:
        out["nota"] = "il file di oggi c'era già, identico: non è stato riscritto"
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_benchmark_results(args) -> int:
    _project, home, _db_path, _state = _settings()
    if args.file:
        path = Path(args.file)
        if not path.exists() and (home / benchmark.REPORTS_DIR / path.name).exists():
            path = home / benchmark.REPORTS_DIR / path.name
    else:
        path = benchmark.latest_filled(home)
        if path is None:
            raise CliError(f"Nessun file compilato in {home / benchmark.REPORTS_DIR}: salva il file del benchmark "
                           "con un nome diverso (per esempio con _compilato in fondo), oppure indica il file.")
    if not path.exists():
        raise CliError(f"File non trovato: {path}")
    try:
        out = benchmark.score(path)
    except (benchmark.FilledFileError, OSError) as exc:
        raise CliError(f"Il file non si legge come benchmark compilato: {exc}") from exc
    try:
        ctx = _open(write=False)
    except CliError as exc:
        out["avviso"] = f"controllo del campione non fatto: {exc.message}"
        _print(out)
        return 0
    out["controllo_campione"] = check = benchmark.check_sample(ctx.conn, path)
    warnings = []
    if not check["stesso_campione"]:
        warnings.append("i documenti del file non sono il campione che questo database dà oggi")
    if check["non_nel_database"]:
        warnings.append(f"{len(check['non_nel_database'])} id_documento non sono nel database")
    if check["dati_crossref_diversi"]:
        warnings.append(f"in {len(check['dati_crossref_diversi'])} righe i dati Crossref del file sono diversi da quelli del database")
    if out["id_ripetuti"]:
        warnings.append("ci sono id_documento ripetuti")
    if warnings:
        out["avvisi"] = warnings
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_search(args) -> int:
    ctx = _open(write=False)
    text = " ".join(args.text)
    exclude = set() if args.tutti else content.not_complete(ctx.conn)
    found = (search_documents(ctx.conn, text, limit=args.limit, exclude=exclude) if args.documents
             else search_pages(ctx.conn, text, limit=args.limit, exclude=exclude))
    _print({"risultati": found, "esclusi_non_completi": len(exclude), **_finish(ctx, wrote=False)})
    return 0


# ---------------------------------------------------------------- article cards (T-019)

def cmd_packets(args) -> int:
    ctx = _open(write=True)
    out: dict = {}
    if args.articoli:
        ids = list(dict.fromkeys(args.articoli))
    elif args.modo:
        # priority asked by pacchetti.cmd (user's request of 2026-10-17 19:13): rivista, equo or casuale
        journals, _problems = layout.read_journal_list(ctx.home / ctx.project.storage.journals_csv)
        pr = card_store.priority_lot(ctx.conn, journals, args.modo, sigla=args.rivista, size=max(0, args.quanti))
        if pr.get("problema"):
            raise CliError(pr["problema"])
        ids = pr["articoli"]
        out["priorita"] = {k: pr[k] for k in ("modo", "rivista", "scelti_per_rivista", "per_rivista", "senza_scheda",
                                               "restano_dopo")}
    else:
        info = card_store.lot(ctx.conn)
        ids = info["aperti"] + info["a_sorte"]
        out["lotto"] = {"testi_aperti": len(info["aperti"]), "a_sorte": len(info["a_sorte"]), "seme": info["seme"],
                        "articoli_completi": info["articoli_completi"], "popolazione_a_sorte": info["popolazione_a_sorte"],
                        "esclusi": info["esclusi"]}
        # the first lot first (it can change when new journals enter the draw), then the next ones until `lotto`
        # packets are open (user's request of 2026-10-16: "fai le schede")
        missing = [a for a in ids if not card_store.current_card(ctx.conn, a)]
        room = args.lotto - len(set(missing) | {a for a in card_store.open_packet_articles(ctx.conn)})
        if room > 0:
            nxt = card_store.next_lot(ctx.conn, args.lotto + len(ids))
            extra = [a for a in nxt["articoli"] if a not in ids][:room]
            ids = ids + extra
            out["lotto"] |= {"successivo": len(extra), "senza_scheda": nxt["senza_scheda"],
                             "restano_dopo": max(0, nxt["senza_scheda"] - len(extra) - len(missing)),
                             "studi_originali": nxt["studi_originali"]}
    res = card_store.issue_packets(ctx.conn, ctx.home, ids, project_name=ctx.project.name,
                                   project_version=ctx.project.version)
    card_store.to_send_dir(ctx.home).mkdir(parents=True, exist_ok=True)
    out |= {"pacchetti": [{k: p[k] for k in ("article_id", "titolo", "pagine", "sezioni", "nuovo_file")} for p in res.issued],
            "pacchetti_testo_aperto_cc": [p["file"] for p in res.issued if p["testo_aperto_cc"]],
            "pacchetti_emessi": len(res.issued), "esecuzioni_nuove": res.created_runs,
            "gia_con_scheda": len(res.skipped), "problemi": res.problems,
            "cartella_pacchetti": str(card_store.packets_dir(ctx.home)),
            "cartella_schede_da_inviare": str(card_store.to_send_dir(ctx.home)),
            "stato": card_store.status(ctx.conn)}
    _print({**out, **_finish(ctx, wrote=bool(res.issued))})
    return 0 if not res.problems else 3


def _card_files(home: Path, given: list[str]) -> list[Path]:
    if not given:
        folder = card_store.to_send_dir(home)
        return sorted(folder.glob("*.json")) if folder.is_dir() else []
    out = []
    for g in given:
        p = Path(g)
        if not p.exists() and (card_store.to_send_dir(home) / p.name).exists():
            p = card_store.to_send_dir(home) / p.name
        out.append(p)
    return out


def cmd_submit(args) -> int:
    ctx = _open(write=True)
    cloud: dict = {}
    if args.dal_cloud and not args.files:
        found, problem = card_store.cloud_cards_from_git()
        cloud = card_store.import_cloud_cards(ctx.home, found)
        if problem:
            cloud["problema"] = problem
    paths = _card_files(ctx.home, args.files)
    subs = [card_store.prepare(ctx.conn, p) for p in paths]
    out: dict = {"schede_trovate": len(paths), "cartella": str(card_store.to_send_dir(ctx.home))}
    if args.dal_cloud and not args.files:
        out["schede_dal_loop"] = cloud
    if any(s.reaches_db for s in subs):
        entry = backups.make_backup(ctx.conn, ctx.state, reason="before_submit_cards")
        out["backup_prima_della_scrittura"] = str(ctx.state.backup_dir / entry["file"])
    results = []
    for s in subs:
        if s.reaches_db:
            summary = card_store.record(ctx.conn, s)
        else:
            already = (s.stop or "").startswith("scheda già accettata")
            summary = {"file": s.path.name, "esito": "già accettata" if already else "rifiutata", "motivi": [s.stop]}
        summary["file_dopo"] = card_store.file_after(s, summary, ctx.home)
        results.append(summary)
    out |= {"accettate": sum(1 for r in results if r["esito"] == "accettata"),
            "rifiutate": sum(1 for r in results if r["esito"] == "rifiutata"),
            "gia_accettate": sum(1 for r in results if r["esito"] == "già accettata"),
            "schede": results, "stato": card_store.status(ctx.conn)}
    if not paths:
        out["nota"] = "Nessuna scheda da inviare in " + out["cartella"]
    _print({**out, **_finish(ctx, wrote=any(s.reaches_db for s in subs))})
    return 0 if not out["rifiutate"] else 3


def cmd_journal_cards(_args) -> int:
    """schede-riviste: submit the journal cards waiting in rapporti/schede_riviste/da_inviare."""
    ctx = _open(write=True)
    folder = journal_cards.to_send_dir(ctx.home)
    folder.mkdir(parents=True, exist_ok=True)
    results = journal_cards.submit_folder(ctx.conn, ctx.home)
    out = {"regole": journal_cards.CARD_VERSION, "cartella": str(folder), "schede_trovate": len(results),
           "accettate": sum(1 for r in results if r.get("esito") == "accettata"),
           "rifiutate": sum(1 for r in results if r.get("esito") == "rifiutata"),
           "non_inviate": sum(1 for r in results if r.get("esito") == "non inviata"),
           "schede": results, "stato": journal_cards.status(ctx.conn)}
    _print({**out, **_finish(ctx, wrote=bool(results))})
    return 0 if not out["rifiutate"] else 3


def cmd_journal_cards_control(_args) -> int:
    """schede-riviste-controllo: the CSV of every current journal card, or the precision of the filled copy."""
    ctx = _open(write=True)
    filled = journal_cards.latest_filled_control(ctx.home, ctx.conn)
    if filled is not None:
        res = journal_cards.score_control(filled)
        res["valori_registrati"] = journal_cards.record_verdicts(ctx.conn, filled)
        _print({"modo": "risultati", **res, **_finish(ctx, wrote=res["valori_registrati"] > 0)})
        return 0
    rows, n_cards = journal_cards.control_rows(ctx.conn)
    if not rows:
        _print({"modo": "niente da controllare", "motivo": "tutti i valori delle schede delle riviste sono già controllati a mano",
                **_finish(ctx, wrote=False)})
        return 0
    path, written = journal_cards.write_control_csv(rows, n_cards, ctx.home)
    _print({"modo": "file da compilare", "file_csv": str(path), "nuovo_file": written, "schede": n_cards,
            "righe": len(rows), "file_da_salvare": str(path.with_name(f"{path.stem}_compilato.csv")),
            **_finish(ctx, wrote=False)})
    return 0


def cmd_check_journal_card(args) -> int:
    """controlla-scheda-rivista FILE: every check of schede-riviste on one card; nothing is written."""
    try:
        data = json.loads(Path(args.scheda).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"File non leggibile: {exc}") from exc
    ctx = _open(write=False)
    outcome, _doc = journal_cards.validate(ctx.conn, data)
    _print({"esito": "accettabile" if outcome.accepted else "da correggere",
            "controlli": {c.name: ("ok" if c.ok else "non superato") for c in outcome.checks},
            "problemi": outcome.problems(), "valori": len(outcome.card.valori) if outcome.card else None,
            **_finish(ctx, wrote=False)})
    return 0 if outcome.accepted else 3


def cmd_check_card(args) -> int:
    try:
        card = json.loads(Path(args.scheda).read_bytes().decode("utf-8-sig"))
        packet = json.loads(Path(args.pacchetto).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"File non leggibile: {exc}") from exc
    outcome = card_store.check_with_packet(card, packet)
    _print({"esito": "accettabile" if outcome.accepted else "da correggere",
            "controlli": {c.name: ("ok" if c.ok else "non superato") for c in outcome.checks},
            "problemi": outcome.problems(), "valori": len(outcome.card.valori) if outcome.card else None})
    return 0 if outcome.accepted else 3


def _send_chat_cards() -> int:
    """chat-schede --invia (user's request of 2026-10-17 19:13): the accepted cards of the free chat into the database,
    with the checks of submit on the text of the database; executor chat_manuale. Copy of the database first."""
    from . import chat_cards
    ctx = _open(write=True)
    folders = chat_cards.ready_to_send(ctx.home)
    subs = [(f, card_store.prepare(ctx.conn, f / chat_cards.CARD)) for f in folders]
    out: dict = {"executor": chat_cards.EXECUTOR, "schede_trovate": len(subs), "cartella": str(chat_cards.chat_dir(ctx.home))}
    if any(s.reaches_db for _f, s in subs):
        entry = backups.make_backup(ctx.conn, ctx.state, reason="before_submit_chat_cards")
        out["backup_prima_della_scrittura"] = str(ctx.state.backup_dir / entry["file"])
    rows = []
    for folder, s in subs:
        if s.reaches_db:
            summary = card_store.record(ctx.conn, s, actor="orpheus chat-schede --invia", executor=chat_cards.EXECUTOR)
        else:
            already = (s.stop or "").startswith("scheda già accettata") or \
                (s.stop or "").startswith("per questo pacchetto c'è già una scheda accettata")
            summary = {"article_id": folder.name, "esito": "già nel database" if already else "rifiutata",
                       "motivi": [] if already else [s.stop]}
        if summary["esito"] in ("accettata", "già nel database"):
            chat_cards.mark_sent(ctx.home, folder, {**summary, "article_id": folder.name})
        else:
            chat_cards.mark_refused(folder, summary)
        # counts only: the reasons quote the article and this output goes into the log sent to GitHub
        rows.append({"article_id": folder.name, "esito": summary["esito"], "valori": summary.get("valori"),
                     "controlli_non_superati": [c for c, o in (summary.get("controlli") or {}).items() if o != "ok"]})
    out |= {"inviate": sum(r["esito"] == "accettata" for r in rows),
            "gia_nel_database": sum(r["esito"] == "già nel database" for r in rows),
            "rifiutate": sum(r["esito"] == "rifiutata" for r in rows), "schede": rows,
            "stato": card_store.status(ctx.conn)}
    if not subs:
        out["nota"] = "Nessuna scheda accettata da inviare in " + out["cartella"]
    _print({**out, **_finish(ctx, wrote=any(s.reaches_db for _f, s in subs))})
    return 0 if not out["rifiutate"] else 3


def cmd_journal_sigle(_args) -> int:
    """riviste-elenco: the abbreviations of the journal list (for the questions of pacchetti.cmd). Read-only."""
    project, home, _db_path, _state = _settings()
    rows, problems = layout.read_journal_list(home / project.storage.journals_csv)
    _print({"sigle": [r["sigla"] for r in rows if r.get("sigla")], "problemi": problems})
    return 0


def cmd_chat_cards(args) -> int:
    from . import chat_cards
    if args.invia:
        return _send_chat_cards()
    _project, home, _db_path, _state = _settings()
    if args.stato:
        _print(chat_cards.status(home))
        return 0
    if args.leggi:
        summary = chat_cards.read_answers(home, args.modello or "", article_id=args.articolo)
        _print({**{k: v for k, v in summary.items() if k != "schede"},
                # no reasons here: they quote the article and this output goes into the log sent to GitHub
                "schede": [{**{k: r.get(k) for k in ("article_id", "esito", "tentativo", "valori",
                                                      "valori_con_citazione", "minuti")},
                            "problemi": len(r.get("problemi") or []),
                            "controlli_non_superati": [c for c, o in (r.get("controlli") or {}).items() if o != "ok"]}
                           for r in summary["schede"]],
                "nota": "schede da controllare a mano: non entrano nel database (ADR-0011); i motivi sono in "
                        "esito.json e correzione.txt, solo sul PC"})
        return 0
    if not chat_cards.pending_packets(home):
        raise CliError("Nessun pacchetto aperto senza scheda in rapporti\\pacchetti: prima `orpheus pacchetti`.")
    _print(chat_cards.prepare(home, how_many=args.quanti, part_chars=args.parte))
    return 0


def cmd_local_cards(args) -> int:
    from . import local_cards
    _project, home, _db_path, _state = _settings()
    if not local_cards.pending_packets(home):
        raise CliError("Nessun pacchetto aperto senza scheda in rapporti\\pacchetti: prima `orpheus pacchetti`.")
    chat = local_cards.ollama_chat(args.modello, host=args.host or local_cards.default_host(), num_ctx=args.num_ctx,
                                   max_tokens=args.max_token)

    def progress(msg: str) -> None:                 # read live by schede_locali.cmd (no text of the articles)
        print(f"[avanzamento] {msg}", file=sys.stderr, flush=True)
    try:
        summary = local_cards.run(home, chat, args.modello, how_many=args.quanti, chunk_chars=args.pezzo,
                                  progress=progress)
    except local_cards.LocalModelError as exc:
        raise CliError(str(exc)) from exc
    _print({**{k: v for k, v in summary.items() if k != "schede"},
            "schede": [{k: r.get(k) for k in ("article_id", "esito", "valori_con_citazione", "valori_scartati",
                                               "pezzi", "pezzi_falliti", "pezzi_tagliati", "secondi")}
                       for r in summary["schede"]],
            "nota": "schede da controllare a mano: non entrano nel database (ADR-0010)"})
    return 0


def cmd_cards_csv(_args) -> int:
    ctx = _open(write=False)
    rows, n_cards = card_store.control_rows(ctx.conn, locator=_locator(ctx))
    if not rows:
        raise CliError("Nessuna scheda accettata nel database: prima invia le schede con schede.cmd.")
    path, written = card_store.write_control_csv(rows, n_cards, ctx.home)
    out = {"file_csv": str(path), "nuovo_file": written, "schede": n_cards, "righe": len(rows),
           "seme": card_store.CONTROL_SEED, "file_da_salvare": str(path.with_name(f"{path.stem}_compilato.csv"))}
    if n_cards < card_store.CONTROL_SIZE:
        out["nota"] = f"solo {n_cards} schede accettate: il campione previsto è di {card_store.CONTROL_SIZE}"
    _print({**out, **_finish(ctx, wrote=False)})
    return 0


def cmd_cards_results(args) -> int:
    _project, home, _db_path, _state = _settings()
    if args.file:
        path = Path(args.file)
        if not path.exists() and (card_store.reports(home) / path.name).exists():
            path = card_store.reports(home) / path.name
    else:
        path = card_store.latest_filled_control(home)
        if path is None:
            raise CliError(f"Nessun file compilato in {card_store.reports(home)}: salva il file del controllo delle schede "
                           "con un nome diverso (per esempio con _compilato in fondo), oppure indica il file.")
    if not path.exists():
        raise CliError(f"File non trovato: {path}")
    try:
        _print(card_store.score_control(path))
    except (benchmark.FilledFileError, OSError) as exc:
        raise CliError(f"Il file non si legge come controllo delle schede compilato: {exc}") from exc
    return 0


# ---------------------------------------------------------------- safety and transfer

def cmd_backup(_args) -> int:
    ctx = _open(write=False)
    if ctx.report.blocking:
        raise CliError("Backup rifiutato: salverebbe tra i riferimenti di questo PC una copia vecchia o divergente. "
                       + ctx.report.message, code=4)
    entry = backups.make_backup(ctx.conn, ctx.state, reason="manual")
    removed = ctx.state.prune(entry["database_id"], ctx.project.local.keep_local_backups)
    ok = entry["integrity"]["integrity"] == "ok" and entry["integrity"]["foreign_key_violations"] == 0
    _print({"backup": str(ctx.state.backup_dir / entry["file"]), "posizione": entry["head_seq"],
            "integrita_della_copia": entry["integrity"], "backup_vecchi_rimossi": removed, **_finish(ctx, wrote=False)})
    return 0 if ok else 2


def cmd_restore_test(args) -> int:
    """Fase 1 gate "backup ripristinato con successo": read-only on the database."""
    ctx = _open(write=False)
    try:
        result = backups.restore_test(ctx.conn, ctx.state, file=args.backup)
    except ValueError as exc:
        raise CliError(f"Prova di ripristino non eseguita: {exc}") from None
    from . import health
    health.record_restore_test(ctx.state, result)     # local folder of the PC, not the database
    _print({**result, **_finish(ctx, wrote=False)})
    return 0 if result["esito"] == "riuscita" else 2


def cmd_salute(_args) -> int:
    """salute (T-045, Fase 8): the health of ORPHEUS on this PC (health-v1); read-only on the database."""
    from . import health
    ctx = _open(write=False, allow_pending=True)      # pending migrations are one of the things it reports
    result = health.check(ctx.conn, state=ctx.state, project=ctx.project, db_path=ctx.db_path, home=ctx.home)
    _print({**result, **_finish(ctx, wrote=False)})
    return 2 if result["esito"] == "errore" else 0


def cmd_export(args) -> int:
    ctx = _open(write=False)
    if ctx.report.blocking:
        raise CliError("Esportazione rifiutata: porterebbe su un altro PC una copia vecchia o divergente. "
                       + ctx.report.message, code=4)
    result = transfer.export_bundle(ctx.conn, home=ctx.home, dest_dir=Path(args.dest),
                                    include_archive=not args.without_archive)
    _print({**result, **_finish(ctx, wrote=False)})
    return 0


def cmd_import(args) -> int:
    project, home, _db_path, state = _settings()
    if args.pdf_only:
        result = transfer.import_pdfs_only(Path(args.bundle), home=home, db_rel=project.storage.db_path)
    else:
        result = transfer.import_bundle(Path(args.bundle), home=home, db_rel=project.storage.db_path, state=state,
                                        discard_local_changes=args.discard_local_changes)
    _print(result)
    return 0


def cmd_condivisione_esporta(args) -> int:
    """condivisione-esporta: package of cards, metadata and parts of the articles for colleagues (T-053). Read only."""
    from . import sharing
    ctx = _open(write=False)
    if ctx.report.blocking:
        raise CliError("Esportazione rifiutata: questa copia del database è vecchia o divergente. " + ctx.report.message,
                       code=4)
    dest = Path(args.dest) if args.dest else ctx.home / sharing.FOLDER_OUT
    try:
        result = sharing.export_package(ctx.conn, dest_dir=dest, note=args.nota)
    except sharing.SharingError as exc:
        raise CliError(exc.message, code=exc.code) from exc
    _print({**result, **_finish(ctx, wrote=False)})
    return 0


def cmd_condivisione_unisci(args) -> int:
    """condivisione-unisci: merge a colleague's package; what this PC already has is kept (T-053)."""
    from . import sharing
    ctx = _open(write=True)
    package = Path(args.pacchetto) if args.pacchetto else sharing.newest_package(ctx.home)
    if package is None:
        raise CliError(f"Nessun pacchetto: metti il file .zip ricevuto in {ctx.home / sharing.FOLDER_IN} e riprova.",
                       code=3)
    out: dict = {}
    if not args.prova:
        entry = backups.make_backup(ctx.conn, ctx.state, reason="prima di condivisione-unisci")
        out["copia_di_sicurezza"] = str(ctx.state.backup_dir / entry["file"])
    try:
        result = sharing.merge_package(ctx.conn, package, dry_run=args.prova)
    except sharing.SharingError as exc:
        raise CliError(exc.message, code=exc.code) from exc
    result["controllo"] = db.integrity_check(ctx.conn)
    _print({**result, **out, **_finish(ctx, wrote=result["scritto"])})
    return 0 if result["problemi_totali"] == 0 else 3


def cmd_accept_state(args) -> int:
    ctx = _open(write=False)
    h = lineage.head(ctx.conn)
    if not args.yes:
        _print({"posizione_attuale": h.seq, "verifica_copia": ctx.report.as_dict(),
                "messaggio": "Nessuna modifica. Con --yes questo PC considererà valido lo stato attuale del database; "
                             "i backup locali più recenti o divergenti restano su disco ma non verranno più usati come riferimento."})
        return 0
    ctx.state.record_seen(h, ctx.db_path, accept=True)
    _print({"accettato": True, "posizione": h.seq, "database_id": h.database_id,
            "nota": "Prima di continuare valuta un `orpheus backup`."})
    return 0


# ---------------------------------------------------------------- checks

def cmd_status(_args) -> int:
    _project, _home, db_path, _state = _settings()
    if not db_path.exists():
        raise CliError(MISSING_DB.format(path=db_path))
    conn = _connect(db_path)
    _print({"migrazioni": db.status(conn), "sconosciute": db.unknown_applied(conn)})
    return 0


def cmd_audit_verify(_args) -> int:
    ctx = _open(write=False)
    rep = audit.verify_chain(ctx.conn)
    _print({**rep.__dict__, **_finish(ctx, wrote=False)})
    return 0 if rep.ok else 2


def cmd_integrity(_args) -> int:
    ctx = _open(write=False)
    rep = db.integrity_check(ctx.conn)
    _print({**rep, **_finish(ctx, wrote=False)})
    return 0 if rep["integrity"] == "ok" and rep["foreign_key_violations"] == 0 else 2


def render_data_dictionary(conn) -> str:
    virtual = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND sql LIKE 'CREATE VIRTUAL TABLE%'")]
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    tables = [t for t in tables if t not in virtual and not any(t.startswith(v + "_") for v in virtual)]
    docs: dict[tuple[str, str], str] = {}
    if "schema_doc" in tables:
        docs = {(r["table_name"], r["column_name"]): r["description"] for r in conn.execute("SELECT * FROM schema_doc")}
    lines = ["# DATA_DICTIONARY", "",
             "Generato automaticamente dallo schema del database con `orpheus data-dictionary`. Non modificare a mano.",
             "", f"Tabelle virtuali di ricerca full-text: {', '.join(virtual) or 'nessuna'}.", ""]
    for t in tables:
        lines += ["", f"## {t}", ""]
        if (t, "") in docs:
            lines += [docs[(t, "")], ""]
        lines += ["| Colonna | Tipo | Obbligatoria | Note |", "|---|---|---|---|"]
        for c in conn.execute(f"PRAGMA table_xinfo('{t}')"):
            note = docs.get((t, c["name"]), "").replace("|", "\\|")
            required = "sì" if c["notnull"] or c["pk"] else "no"
            lines.append(f"| {c['name']} | {c['type']} | {required} | {note} |")
    return "\n".join(lines) + "\n"


def cmd_data_dictionary(_args) -> int:
    ctx = _open(write=False)
    sys.stdout.write(render_data_dictionary(ctx.conn))
    _finish(ctx, wrote=False)
    return 0


def cmd_doctor(_args) -> int:
    report: dict = {}
    ok = True
    try:
        project, sources, models = load_project(), load_sources(), load_models()
        violations = check_budget_policy(project, sources, models)
        report["config"] = {
            "project": project.name, "version": project.version, "research_domains": project.research_domains,
            "paid_services_allowed": project.limits.paid_services_allowed,
            "sources_enabled": [s.id for s in sources if s.enabled],
            "executors_enabled": [e.id for e in models.executors if e.enabled],
        }
        report["budget_policy"] = "compliant" if not violations else violations
        ok &= not violations
    except Exception as exc:  # report, do not hide
        _print({"config_error": repr(exc)})
        return 1
    report["secrets_present"] = load_secrets().present()
    home, local = config.home(), config.local_dir()
    db_path = home / project.storage.db_path
    report["cartelle"] = {"ORPHEUS_HOME": str(home), "ORPHEUS_LOCAL_DIR": str(local), "database": str(db_path)}
    warnings = config.path_warnings(home, local, home_configured=config.home_is_configured())
    if warnings:
        report["avvisi_cartelle"] = warnings
        ok = False
    state = LocalState(local)
    try:
        if not db_path.exists():
            report["database"] = MISSING_DB.format(path=db_path)
            ok = False
        else:
            conn = _connect(db_path)
            report["migrazioni"] = db.status(conn)
            unknown = db.unknown_applied(conn)
            if unknown or any(m["state"] != "applied" for m in report["migrazioni"]):
                ok = False
                report["migrazioni_sconosciute"] = unknown
            report["integrita"] = db.integrity_check(conn)
            chain = audit.verify_chain(conn)
            report["catena_audit_valida"] = chain.ok
            check = lineage.check(conn, state)
            report["verifica_copia"] = check.as_dict()
            ok &= report["integrita"]["integrity"] == "ok" and chain.ok and not check.blocking
            missing = transfer.missing_archive_files(conn, home)
            report["pdf_archivio_mancanti"] = len(missing)
            if missing:
                ok = False
                report["esempi_pdf_mancanti"] = missing[:10]
            h = lineage.head(conn)
            if h.database_id:
                newest = state.newest_backup(h.database_id)
                report["backup_locali"] = {"numero": len(state.backups(h.database_id, include_superseded=True)),
                                           "piu_recente": newest["created_at"] if newest else None}
    except Exception as exc:
        ok = False
        report["database_error"] = f"{type(exc).__name__}: {exc}"
    journals, problems = layout.read_journal_list(home / project.storage.journals_csv)
    report["riviste_prioritarie"] = {"righe": len(journals), **({"problemi": problems} if problems else {})}
    _print(report)
    return 0 if ok else 1


# ---------------------------------------------------------------- entry point

def main(argv: list[str] | None = None) -> int:
    # Windows defaults to a legacy code page when output is redirected to a file;
    # article text can contain any Unicode character, so always write UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="orpheus")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in [("doctor", cmd_doctor), ("migrate", cmd_migrate), ("status", cmd_status),
                     ("audit-verify", cmd_audit_verify), ("integrity", cmd_integrity), ("backup", cmd_backup),
                     ("data-dictionary", cmd_data_dictionary), ("ingest-inbox", cmd_ingest_inbox),
                     ("quality-report", cmd_quality_report), ("report", cmd_report), ("riviste", cmd_riviste), ("separa-non-completi", cmd_separate),
                     ("allinea-accesso", cmd_align_access), ("doppioni", cmd_doppioni),
                     ("scrivi-autori", cmd_write_authors), ("riferimenti", cmd_riferimenti)]:
        sub.add_parser(name).set_defaults(func=fn)
    p = sub.add_parser("init")
    p.add_argument("--yes", action="store_true", help="create a new database even if this PC already knows another one")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("ingest-pdf")
    p.add_argument("paths", nargs="+")
    p.add_argument("--kind", choices=["article", "author_guidelines", "other"], default="article")
    p.add_argument("--no-archive", action="store_true", help="do not copy files into the archive")
    p.set_defaults(func=cmd_ingest_pdf)
    p = sub.add_parser("ocr")
    p.add_argument("paths", nargs="+")
    p.add_argument("--lingua", default="eng", help="Tesseract language code(s), e.g. eng or eng+ita")
    p.set_defaults(func=cmd_ocr)
    p = sub.add_parser("search")
    p.add_argument("text", nargs="+")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--documents", action="store_true", help="one line per document (best page first) instead of per page")
    p.add_argument("--tutti", action="store_true", help="also conference abstracts and single pages (not complete articles)")
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("openalex")
    p.add_argument("--prova", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_openalex)
    p = sub.add_parser("verify-dois")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--ricontrolla", action="store_true", help="ask Crossref again for DOIs not found or not matching")
    g.add_argument("--rivaluta", action="store_true", help="no network: re-apply the current rules to stored answers")
    p.add_argument("--limit", type=int, default=None, help="verify at most N documents in this run")
    p.set_defaults(func=cmd_verify_dois)
    p = sub.add_parser("cerca-doi")
    p.add_argument("--ricontrolla", action="store_true", help="search again the PDFs whose DOI found earlier was not confirmed")
    p.add_argument("--limit", type=int, default=None, help="search at most N documents in this run")
    p.set_defaults(func=cmd_search_dois)
    p = sub.add_parser("scrivi-riviste")
    p.add_argument("--prova", action="store_true", help="show what would be written, write nothing")
    p.set_defaults(func=cmd_write_journals)
    p = sub.add_parser("sistema-riviste")
    p.add_argument("--prova", action="store_true", help="show what would be changed, change nothing")
    p.set_defaults(func=cmd_fix_journals)
    p = sub.add_parser("riconosci-riviste")
    p.add_argument("--prova", action="store_true", help="no network, write nothing: the journals that would be added")
    p.add_argument("--senza-openalex", action="store_true", help="do not ask OpenAlex for the site of the journal")
    p.set_defaults(func=cmd_recognise_journals)
    p = sub.add_parser("sposta-articoli")
    p.add_argument("--prova", action="store_true", help="show what would be moved, move nothing")
    p.add_argument("--annulla", default=None, help="registro (rapporti/spostamenti/*.json) to reverse, or 'ultimo'")
    p.set_defaults(func=cmd_move_articles)
    p = sub.add_parser("metriche-riviste")
    p.add_argument("--prova", action="store_true", help="no network, write nothing: what would be read and asked")
    p.add_argument("--senza-openalex", action="store_true", help="only the SCImago file, no network")
    p.set_defaults(func=cmd_journal_metrics)
    p = sub.add_parser("segmenta")
    p.add_argument("--prova", action="store_true", help="show what would be written, write nothing")
    p.set_defaults(func=cmd_segments)
    p = sub.add_parser("vettori")
    p.add_argument("--prova", action="store_true", help="how many vectors are missing; nothing downloaded or written")
    p.add_argument("--solo-scarica", action="store_true", help="only download the model")
    p.set_defaults(func=cmd_vectors)
    p = sub.add_parser("cerca-testi")
    p.add_argument("text", nargs="+")
    p.add_argument("--modo", choices=["ibrida", "parole", "significato"], default="ibrida")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search_texts)
    p = sub.add_parser("benchmark-ricerca")
    p.add_argument("file", nargs="?", default=None, help="the filled questions file (default: the newest in rapporti)")
    p.set_defaults(func=cmd_search_benchmark)
    p = sub.add_parser("cruscotto")
    p.add_argument("--porta", type=int, default=None, help="port on 127.0.0.1 (default: the first free from 8765)")
    p.add_argument("--senza-browser", action="store_true", help="do not open the browser")
    p.add_argument("--comandi", default=None, help="orpheus_comandi.ps1 of this PC (for the button Avvia lavora)")
    p.set_defaults(func=cmd_cruscotto)
    p = sub.add_parser("manoscritto")
    p.add_argument("file", nargs="?", default=None, help="the .docx (default: ORPHEUS_MANOSCRITTO)")
    p.add_argument("--rivista", action="append", default=None, help="sigla or journal id (repeat it); 'tutte' = all")
    p.add_argument("--tipo", choices=["auto", "originale", "revisione", "protocollo"], default="auto")
    p.add_argument("--riviste", action="store_true", help="only list the journals with a card")
    p.add_argument("--senza-rapporto", action="store_true", help="do not write the report in rapporti/manoscritti")
    p.add_argument("--verifica-riferimenti", action="store_true",
                   help="check the references with ORPHEUS and Crossref (network; only DOIs and cited titles)")
    p.set_defaults(func=cmd_manoscritto)
    p = sub.add_parser("scheda-unica")
    p.add_argument("--converti", action="store_true", help="copy the cards-v1 cards into the unified card (backup first)")
    p.add_argument("--esporta", default=None, metavar="ART", help="export the card of one article (JSON + CSV)")
    p.add_argument("--tutte", action="store_true", help="export every article with a unified card")
    p.add_argument("--correggi", nargs=2, metavar=("ART", "CAMPO"), help="the user's decision on one value")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--valore", default=None, help="the corrected value, as written in the article")
    g.add_argument("--conferma", action="store_true", help="confirm the current value")
    g.add_argument("--non-riportato", action="store_true", help="the article does not report it")
    p.add_argument("--citazione", default=None)
    p.add_argument("--pagina", type=int, default=None)
    p.add_argument("--ordine", type=int, default=1, help="number of the value when the field repeats (default 1)")
    p.add_argument("--nota", default=None)
    p.set_defaults(func=cmd_scheda_unica)
    p = sub.add_parser("revisione-sistematica")
    p.add_argument("file", nargs="?", default=None, help="the .docx of the review (default: ORPHEUS_MANOSCRITTO)")
    p.add_argument("--cartella", action="store_true", help="only print the folder of the manuscripts")
    p.add_argument("--senza-rapporto", action="store_true", help="do not write the report in rapporti/manoscritti")
    p.set_defaults(func=cmd_revisione_sistematica)
    p = sub.add_parser("revisione")
    p.add_argument("file", nargs="?", default=None, help="the .docx (default: ORPHEUS_MANOSCRITTO)")
    p.add_argument("--rivista", default=None, help="sigla of ONE journal with a card")
    p.add_argument("--tipo", choices=["auto", "originale", "revisione", "protocollo"], default="auto")
    p.add_argument("--controlla", default=None, help="(for Claude) check a revisione.json against --pacchetto")
    p.add_argument("--pacchetto", default=None)
    p.set_defaults(func=cmd_revisione)
    for name, func in (("lettera", cmd_lettera), ("risposta", cmd_risposta)):
        p = sub.add_parser(name)
        p.add_argument("file", nargs="?", default=None, help="the .docx (default: ORPHEUS_MANOSCRITTO); risposta: the "
                                                             "REVISED manuscript")
        p.add_argument("--rivista", default=None, help="sigla of ONE journal with a card")
        p.add_argument("--tipo", choices=["auto", "originale", "revisione", "protocollo"], default="auto")
        p.add_argument("--controlla", default=None, help="(for Claude) check the JSON against --pacchetto")
        p.add_argument("--pacchetto", default=None)
        p.set_defaults(func=func)
    p = sub.add_parser("validazione")
    p.add_argument("file", nargs="?", default=None, help="the .docx (default: ORPHEUS_MANOSCRITTO)")
    p.add_argument("--rivista", default=None, help="sigla of ONE journal with a card")
    p.add_argument("--tipo", choices=["auto", "originale", "revisione", "protocollo"], default="auto")
    p.set_defaults(func=cmd_validazione)
    p = sub.add_parser("nuovo-articolo")
    p.add_argument("nome", nargs="?", default=None, help="project folder in nuovi_articoli (default: ORPHEUS_PROGETTO)")
    p.add_argument("--rivista", default=None, help="sigla of the journal (when the project is created)")
    p.add_argument("--tipo", choices=["originale", "revisione", "protocollo"], default="originale",
                   help="type of article (when the project is created)")
    p.add_argument("--approva", metavar="SEZIONE", default=None, help="the user approves an accepted section")
    p.add_argument("--controlla", metavar="FILE", default=None, help="check one section file (for Claude), nothing moved")
    p.add_argument("--pacchetto", metavar="FILE", default=None, help="with --controlla: the writing packet")
    p.add_argument("--elenco", action="store_true", help="how many projects there are")
    p.add_argument("--bozza", action="store_true", help="the Word draft (bozza.docx) when every section is approved")
    p.set_defaults(func=cmd_nuovo_articolo)
    p = sub.add_parser("lavora")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--piano", action="store_true", help="read-only: steps, run in progress or interrupted")
    g.add_argument("--passo", choices=[s.key for s in engine.STEPS], help="run one step (lavora.cmd)")
    g.add_argument("--fine", action="store_true", help="end of the run: summary and lock released")
    p.add_argument("--conti", action="store_true", help="with --piano: count what is missing for each step")
    p.add_argument("--giro", default=None, help="name of the run (lavora.cmd gives one per double click)")
    p.add_argument("--pid", type=int, default=None, help="process that owns the run (default: this one)")
    p.add_argument("--gruppo", choices=list(engine.GROUPS), default="tutto",
                   help="with --piano: only the steps of a group (articoli, riviste, conoscenza; default: tutto)")
    p.set_defaults(func=cmd_lavora)
    sub.add_parser("affermazioni").set_defaults(func=cmd_claims)
    sub.add_parser("affermazioni-controllo").set_defaults(func=cmd_claims_control)
    p = sub.add_parser("controlla-affermazioni")
    p.add_argument("file")
    p.set_defaults(func=cmd_check_claims)
    p = sub.add_parser("linee-guida")
    p.add_argument("--prova", action="store_true", help="show what would be written, write nothing")
    p.set_defaults(func=cmd_policy_versions)
    p = sub.add_parser("verifica-pubmed")
    p.add_argument("--ricontrolla", action="store_true", help="ask PubMed again for articles not found, discordant or ambiguous")
    p.add_argument("--prova", action="store_true", help="no network and no writes: what would be asked, requests and minutes")
    p.add_argument("--limit", type=int, default=None, help="check at most N articles in this run")
    p.set_defaults(func=cmd_verify_pubmed)
    p = sub.add_parser("europepmc")
    p.add_argument("--prova", action="store_true", help="no network: articles to ask, minimum requests and minutes")
    p.add_argument("--salva", action="store_true", help="write the outcomes, PMCID, licences and CC full texts (0006)")
    p.add_argument("--ricontrolla", action="store_true", help="with --salva: ask again not found / discordant / ambiguous")
    p.add_argument("--limit", type=int, default=None, help="ask about at most N articles in this run")
    p.set_defaults(func=cmd_europepmc)
    p = sub.add_parser("verifica-citazioni")
    p.add_argument("--prova", action="store_true", help="no network and no writes: articles, references, DOIs, requests")
    p.add_argument("--limit", type=int, default=None, help="check at most N articles in this run")
    p.add_argument("--rivaluta", action="store_true", help="no network: apply the current rules to the rows already written")
    p.set_defaults(func=cmd_verify_citations)
    p = sub.add_parser("benchmark-csv")
    p.add_argument("--righe", type=int, default=benchmark.SIZE, help="number of records (default 50)")
    p.set_defaults(func=cmd_benchmark_csv)
    p = sub.add_parser("benchmark-risultati")
    p.add_argument("file", nargs="?", default=None, help="the filled file (default: the newest one in rapporti)")
    p.set_defaults(func=cmd_benchmark_results)
    p = sub.add_parser("pacchetti")
    p.add_argument("--articoli", nargs="+", default=None, metavar="ID", help="these articles instead of the first lot")
    p.add_argument("--lotto", type=int, default=card_store.NEXT_LOT,
                   help="size of each lot after the first (default %(default)s)")
    p.add_argument("--modo", choices=card_store.PRIORITY_MODES, default=None,
                   help="priority of the new packets: one journal (rivista), the same number per journal (equo) or "
                        "the order of the draw (casuale); without it, the first lot and then lots up to --lotto")
    p.add_argument("--rivista", default=None, help="sigla of the journal (with --modo rivista)")
    p.add_argument("--quanti", type=int, default=10, help="how many new packets with --modo (default %(default)s)")
    p.set_defaults(func=cmd_packets)
    p = sub.add_parser("submit")
    p.add_argument("files", nargs="*", help="card files (default: every .json in rapporti/schede/da_inviare)")
    p.add_argument("--dal-cloud", action="store_true",
                   help="first copy into da_inviare the cards the hourly loop wrote in origin/main:schede_cloud/ (git)")
    p.set_defaults(func=cmd_submit)
    p = sub.add_parser("controlla-scheda")
    p.add_argument("scheda")
    p.add_argument("pacchetto")
    p.set_defaults(func=cmd_check_card)
    p = sub.add_parser("schede-locali")
    p.add_argument("--modello", default="qwen2.5:7b-instruct")
    p.add_argument("--quanti", type=int, default=10)
    p.add_argument("--host", default=None, help="indirizzo di Ollama (predefinito: OLLAMA_HOST o 127.0.0.1:11434)")
    p.add_argument("--num-ctx", dest="num_ctx", type=int, default=8192)
    p.add_argument("--pezzo", type=int, default=12000, help="caratteri di testo per chiamata")
    p.add_argument("--max-token", dest="max_token", type=int, default=2048, help="lunghezza massima di una risposta")
    p.set_defaults(func=cmd_local_cards)
    p = sub.add_parser("chat-schede")
    p.add_argument("--quanti", type=int, default=1)
    p.add_argument("--parte", type=int, default=20000, help="caratteri di testo per messaggio")
    p.add_argument("--leggi", action="store_true", help="legge le risposte incollate in risposta.txt")
    p.add_argument("--modello", default=None, help="nome e versione della chat usata, come li dichiara l'utente")
    p.add_argument("--stato", action="store_true", help="solo i conteggi, senza preparare né leggere")
    p.add_argument("--articolo", default=None, help="con --leggi: solo questo articolo")
    p.add_argument("--invia", action="store_true",
                   help="manda nel database le schede accettate (stessi controlli di submit; copia di sicurezza prima)")
    p.set_defaults(func=cmd_chat_cards)
    sub.add_parser("riviste-elenco").set_defaults(func=cmd_journal_sigle)
    sub.add_parser("schede-csv").set_defaults(func=cmd_cards_csv)
    sub.add_parser("schede-riviste").set_defaults(func=cmd_journal_cards)
    sub.add_parser("schede-riviste-controllo").set_defaults(func=cmd_journal_cards_control)
    p = sub.add_parser("controlla-scheda-rivista")
    p.add_argument("scheda")
    p.set_defaults(func=cmd_check_journal_card)
    p = sub.add_parser("schede-risultati")
    p.add_argument("file", nargs="?", default=None, help="the filled file (default: the newest one in rapporti)")
    p.set_defaults(func=cmd_cards_results)
    p = sub.add_parser("prova-ripristino")
    p.add_argument("--backup", default=None, help="this backup file (name in backups/index.json) instead of the newest")
    p.set_defaults(func=cmd_restore_test)
    sub.add_parser("salute").set_defaults(func=cmd_salute)
    p = sub.add_parser("condivisione-esporta", help="pacchetto per i colleghi: schede, metadati, pezzi di testo")
    p.add_argument("--dest", default=None, help="cartella (predefinita: ORPHEUS_HOME/condivisione)")
    p.add_argument("--nota", default=None, help="nota scritta nel pacchetto")
    p.set_defaults(func=cmd_condivisione_esporta)
    p = sub.add_parser("condivisione-unisci", help="unisce il pacchetto di un collega; quello che hai già resta tuo")
    p.add_argument("pacchetto", nargs="?", default=None,
                   help="file .zip (predefinito: il più recente in ORPHEUS_HOME/inbox/condivisione)")
    p.add_argument("--prova", action="store_true", help="dice cosa entrerebbe senza scrivere nulla")
    p.set_defaults(func=cmd_condivisione_unisci)
    p = sub.add_parser("export")
    p.add_argument("dest", help="folder where the .zip bundle is written (e.g. a USB stick)")
    p.add_argument("--without-archive", action="store_true", help="database only, without the archived PDFs")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("import")
    p.add_argument("bundle", help="the .zip created by `orpheus export`, or a backup .db (restore)")
    p.add_argument("--discard-local-changes", action="store_true",
                   help="replace an older or divergent local database anyway (a local backup is made first)")
    p.add_argument("--pdf-only", action="store_true",
                   help="only copy the PDFs missing from the archive; the database is not touched")
    p.set_defaults(func=cmd_import)
    p = sub.add_parser("accept-state")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_accept_state)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        _print({"errore": exc.message, **exc.details})
        return exc.code
    except transfer.TransferError as exc:
        _print({"errore": exc.message, **exc.details})
        return exc.code
    finally:
        _close_all()


if __name__ == "__main__":
    raise SystemExit(main())
